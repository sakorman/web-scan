"""Audit fixed representatives with native projections and held-out sensor pixels.

Diagnostic only. RGB selection is independent of sensor values; a sensor pass
requires the same three views, including the source, with pairwise parallax.
"""
import argparse
import itertools
import time
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.benchmark_depth_consistency import camera, project, unproject, check_pair, grid
from scripts.benchmark_depth_groups import diverse_three
from scripts.benchmark_depth_overlap import checked_map, nearest, observations, read
from scripts.benchmark_depth_prior import digest, fit_cells, sample_depth, save
from scripts.benchmark_depth_support import raw_project, raw_unproject, radial_camera, unique_rows
from scripts.benchmark_sensor_depth import sensor_samples


SENSOR_RADIUS = .01


def support_mask(projected_z, sampled_z, inside, tolerance):
    projected_z, sampled_z = np.asarray(projected_z, float), np.asarray(sampled_z, float)
    inside = np.asarray(inside, bool)
    if projected_z.shape != sampled_z.shape or inside.shape != projected_z.shape:
        raise ValueError('Projection arrays differ')
    if not np.isfinite(tolerance) or not 0 < tolerance < 1:
        raise ValueError('Invalid tolerance')
    valid = inside & np.isfinite(projected_z) & (projected_z > 0)
    valid &= np.isfinite(sampled_z) & (sampled_z > 0)
    error = np.full(projected_z.shape, np.inf)
    error[valid] = np.abs(sampled_z[valid] / projected_z[valid] - 1)
    return valid & (error <= tolerance)


def calibration(view):
    if view['camera_model'] == 'RADIAL':
        return radial_camera(view), raw_project, raw_unproject
    return camera(view), project, unproject


def source_triple(points, source, centers, votes, degrees=1.):
    """Source plus two voters; all three ray pairs must satisfy the angle."""
    result = np.zeros(len(points), bool)
    for i, point in enumerate(points):
        s = source[i]
        if not votes[i, s]:
            continue
        targets = np.flatnonzero(votes[i] & (np.arange(len(centers)) != s))
        result[i] = any(diverse_three(point, centers[[s, a, b]], degrees)
                        for a, b in itertools.combinations(targets, 2))
    return result


def sensor_audit(points, xy, projected_z, depth, cal, lift, scale, cloud):
    """Nearest sensor pixel center; fit cells cannot validate their fitted scale."""
    measured, available = sensor_samples(depth, xy)
    available &= np.isfinite(projected_z) & (projected_z > 0)
    centers = np.full(xy.shape, np.nan)
    centers[available] = np.floor(xy[available]) + .5
    check = np.zeros(len(points), bool)
    check[available] = ~fit_cells(centers[available], depth.shape[1], depth.shape[0])
    spatial, far = np.zeros(len(points), bool), np.zeros(len(points), bool)
    good = available & check
    if good.any():
        z = measured[good] / scale
        truth = lift(centers[good], z, cal)
        finite = np.isfinite(truth).all(axis=1)
        idx = np.flatnonzero(good)[finite]
        z, truth = z[finite], truth[finite]
        spatial[idx] = np.linalg.norm(points[idx] - truth, axis=1) / z <= SENSOR_RADIUS
        far[idx] = nearest(truth, cloud) / z > SENSOR_RADIUS
    return dict(available=available, check=check, spatial=spatial, far=far, measured=measured)


def audit(points, source, views, depths, config, sensors=None, scale=None, cloud=None):
    """Keep loose historical counts separate from geometric and sensor criteria."""
    n, v = len(points), len(views)
    cals = [calibration(view) for view in views]
    centers = np.array([-c[0][1][:, :3].T @ c[0][1][:, 3] for c in cals])
    rgb = np.zeros((n, v), bool)
    loose = np.zeros_like(rgb)
    available = np.zeros_like(rgb)
    conflict = np.zeros_like(rgb)
    measured = {k: np.zeros_like(rgb) for k in ('available', 'check', 'spatial', 'far', 'legacy', 'legacy_check')}
    for t, (cal, proj, lift) in enumerate(cals):
        xy, z = proj(points, cal)
        prior, inside = sample_depth(depths[t], xy)
        available[:, t] = inside & (z > 0) & np.isfinite(prior) & (prior > 0)
        loose[:, t] = support_mask(z, prior, available[:, t], .05)
        for s in range(v):
            subset = source == s
            if s == t:
                rgb[subset, t] = True  # The representative itself is the source prediction.
                continue
            source_cal, source_proj, _ = cals[s]
            source_xy, source_z = source_proj(points[subset], source_cal)
            masks = check_pair(source_xy, source_z, source_cal, cal, depths[t], config,
                               project_fn=proj, unproject_fn=lift)
            rgb[subset, t] = masks['supported']
            conflict[subset, t] = masks['front_depth_conflict']
        if sensors is not None:
            flags = sensor_audit(points, xy, z, sensors[t], cal, lift, scale, cloud)
            for key in ('available', 'check', 'spatial', 'far'):
                measured[key][:, t] = flags[key]
            # Reproduce 11/05 exactly for comparison only; it used fit cells and
            # conditioned sensor availability on the monocular prior being valid.
            measured['legacy'][:, t] = support_mask(z, flags['measured'] / scale,
                                                    flags['available'] & available[:, t], .05)
            measured['legacy_check'][:, t] = measured['legacy'][:, t] & flags['check']
    other = np.arange(v)[None, :] != source[:, None]
    geometric = source_triple(points, source, centers, rgb, config['min_parallax_degrees'])
    clean = geometric & ~conflict.any(axis=1)
    summary = dict(groups=n, attempted_other_pairs=int(other.sum()),
                   sampleable_other_pairs=int((available & other).sum()),
                   legacy_depth_only_two=int(((loose & other).sum(axis=1) >= 2).sum()),
                   roundtrip_two=int(((rgb & other).sum(axis=1) >= 2).sum()),
                   source_triple=int(geometric.sum()), clean_source_triple=int(clean.sum()),
                   front_conflict_groups=int(conflict.any(axis=1).sum()))
    arrays = dict(world=points, source=source, rgb=rgb, conflict=conflict, clean=clean)
    if sensors is not None:
        sensor_valid = measured['available'] & measured['check']
        spatial = sensor_valid & measured['spatial']
        strict = spatial & measured['far']
        flags = {}
        for label, votes in [('available', sensor_valid), ('spatial', spatial), ('far', strict)]:
            flags[label] = clean & source_triple(points, source, centers, rgb & votes,
                                                config['min_parallax_degrees'])
        summary.update(legacy_sensor_two=int(((measured['legacy'] & other).sum(axis=1) >= 2).sum()),
                       legacy_sensor_two_check_only=int(((measured['legacy_check'] & other).sum(axis=1) >= 2).sum()),
                       sensor_available_other_pairs=int((measured['available'] & other).sum()),
                       sensor_fit_other_pairs=int((measured['available'] & ~measured['check'] & other).sum()),
                       sensor_check_other_pairs=int((sensor_valid & other).sum()),
                       sensor_missing_other_pairs=int((~measured['available'] & other).sum()),
                       joint_check_three=int(flags['available'].sum()),
                       joint_spatial_three=int(flags['spatial'].sum()),
                       joint_far_three=int(flags['far'].sum()))
        arrays.update({f'sensor_{k}': a for k, a in measured.items()})
        arrays.update({f'joint_{k}': a for k, a in flags.items()})
    return summary, arrays


def verify_inputs(args):
    """Bind every input to the same frozen reconstruction before measuring it."""
    samples = read(args.samples / 'samples.json')
    groups = read(args.groups / 'groups.json')
    cross, analysis = read(args.consistency), read(args.analysis)
    overlap = samples['overlap']
    if (groups['samples_sha256'] != digest(args.samples / 'samples.json')
            or overlap['consistency_sha256'] != digest(args.consistency)
            or cross['status'] != 'complete' or cross['analysis_sha256'] != digest(args.analysis)
            or cross['anchors_sha256'] != digest(args.anchors)
            or analysis['anchor_sha256'] != digest(args.anchors)
            or analysis['inference']['anchor_sha256'] != digest(args.anchors)
            or analysis['inference']['status'] != 'complete'):
        raise ValueError('Input identity chain differs')
    rows = sorted(samples['views'], key=lambda r: r['name'])
    names = [r['name'] for r in rows]
    if (names != groups['source_views'] or len(set(names)) != len(rows)
            or len({r['image_sha256'] for r in rows}) != len(rows)):
        raise ValueError('Source view identities differ or repeat')
    job = args.work.name
    select = lambda records: unique_rows([r for r in records if r['job'] == job])
    views = select(read(args.anchors)['views'])
    align = select(analysis['views'])
    predictions = select(analysis['inference']['views'])
    scene = next(s for s in cross['scenes'] if s['job'] == job)
    if set(names) != {r['name'] for r in scene['views']} or set(names) != {k for k, r in align.items() if r['passed']}:
        raise ValueError('Selected view set differs')
    kinds = {views[n]['camera_model'] for n in names}
    if kinds not in ({'RADIAL'}, {'PINHOLE'}):
        raise ValueError('Mixed camera domains')
    sparse = args.work / ('sfm/chosen' if kinds == {'RADIAL'} else 'sfm/undistorted/sparse')
    if ({p.name: digest(p) for p in sparse.glob('*.bin')} != overlap['sparse_sha256']
            or digest(args.work/'result.json') != overlap['result_sha256']
            or digest(args.work/'sfm/undistorted/sparse/points3D.bin') != overlap['training_points_sha256']):
        raise ValueError('Frozen reconstruction differs')
    import pycolmap
    model = pycolmap.Reconstruction(sparse)
    images = {im.name: im for im in model.images.values()}
    result = read(args.work/'result.json')
    held = {Path(v['name']).stem for v in result['report']['evaluation_images']}
    if result['status'] != 'complete' or not result['report']['evaluation_verified'] or held & {Path(n).stem for n in names}:
        raise ValueError('Expected verified training views')
    for r in result['inputs']:
        if digest(args.work/'input'/r['name']) != r['sha256']:
            raise ValueError('Original photograph differs')
    chunks, depths, sources = [], [], []
    for i, row in enumerate(rows):
        n, view = row['name'], views[row['name']]
        observations(model, images[n], view)
        cal = calibration(view)[0]
        center = -cal[1][:, :3].T @ cal[1][:, 3]
        if (digest(view['image']) != row['image_sha256'] or row['image_sha256'] != view['image_sha256']
                or predictions[n]['image_sha256'] != row['image_sha256']
                or not np.allclose(row['center'], center, rtol=0, atol=1e-12)):
            raise ValueError('Camera or photograph identity differs')
        path = args.predictions / predictions[n]['prediction']
        if digest(path) != predictions[n]['prediction_sha256']:
            raise ValueError('Prediction identity differs')
        raw = np.load(path, allow_pickle=False)
        if raw.shape != (view['height'], view['width']):
            raise ValueError('Prediction dimensions differ')
        inv = align[n]['scale'] * raw.astype(float) + align[n]['shift']
        depth = np.full(inv.shape, np.nan)
        np.divide(1, inv, out=depth, where=np.isfinite(inv) & (inv > 0))
        depths.append(depth)
        values = checked_map(args.samples, row)
        keep = (values['image_distance'] > .01) & (values['space_distance'] > .01) & ~values['conflict']
        order = np.argsort(values['grid_index'][keep], kind='stable')
        chunk = {k: values[k][keep][order] for k in ('world', 'z', 'grid_index')}
        xy = grid(view['width'], view['height'], cross['config']['grid_step'])[0][chunk['grid_index']]
        actual_xy, actual_z = calibration(view)[1](chunk['world'], cal)
        if (not np.allclose(actual_xy, xy, rtol=0, atol=1e-5)
                or not np.allclose(actual_z, chunk['z'], rtol=1e-12, atol=1e-12)):
            raise ValueError('Candidate does not project to frozen source pixel')
        chunks.append(chunk)
        sources.append(np.full(len(chunk['z']), i, int))
    values = {k: np.concatenate([c[k] for c in chunks]) for k in chunks[0]}
    if len(values['z']) != overlap['pooled']['0.01']['both_far_no_front_conflict']:
        raise ValueError('Candidate count differs')
    return samples, groups, [views[n] for n in names], depths, values, np.concatenate(sources), model, cross['config']


def load_sensors(args, samples, views):
    if bool(args.sensor_manifest) != bool(args.sensor_evaluation):
        raise ValueError('Sensor manifest and evaluation must be supplied together')
    if not args.sensor_manifest:
        if samples['overlap']['sensor_sha256'] is not None:
            raise ValueError('Missing sensor audit inputs')
        return None, None
    evaluation, manifest = read(args.sensor_evaluation), read(args.sensor_manifest)
    if (evaluation['status'] != 'complete' or evaluation['manifest_sha256'] != digest(args.sensor_manifest)
            or evaluation['analysis_sha256'] != digest(args.analysis)
            or samples['overlap']['sensor_sha256'] != digest(args.sensor_evaluation)
            or manifest['anchor_sha256'] != digest(args.anchors)):
        raise ValueError('Sensor identity chain differs')
    scale = evaluation['scale_m_per_sfm_unit']
    if not np.isfinite(scale) or scale <= 0 or scale != samples['overlap']['scale_m_per_sfm_unit']:
        raise ValueError('Invalid or inconsistent sensor scale')
    if evaluation['config']['depth_factor'] != 5000:
        raise ValueError('Expected native TUM depth units')
    rows = unique_rows(manifest['views'])
    if rows.keys() != {v['name'] for v in views}:
        raise ValueError('Sensor view set differs')
    depths = []
    for view in views:
        row = rows[view['name']]
        path = args.sensor_manifest.parent / row['depth']
        if (digest(path) != row['depth_sha256'] or not np.isfinite(row['time_delta_seconds'])
                or not 0 <= row['time_delta_seconds'] <= evaluation['config']['association_max_seconds']):
            raise ValueError('Sensor timestamp or depth differs')
        with Image.open(path) as image:
            depth = np.asarray(image)
        if depth.shape != (view['height'], view['width']):
            raise ValueError('Sensor dimensions differ')
        depths.append(depth)
    return depths, scale


def run(args):
    if args.output.exists() or args.output.with_suffix('.maps').exists():
        raise FileExistsError('Use a new output path; historical evidence is retained')
    started = time.monotonic()
    samples, groups, views, depths, values, sources, model, config = verify_inputs(args)
    sensors, scale = load_sensors(args, samples, views)
    cloud = np.array([p.xyz for p in model.points3D.values()])
    maps = args.output.with_suffix('.maps')
    maps.mkdir(parents=True, exist_ok=False)
    output = dict(schema=2, job=args.work.name, source_views=[v['name'] for v in views], scope='fixed RGB representatives; sensor audit only; no point injection',
                  config=dict(cross_view=config, sensor_radius=SENSOR_RADIUS, sensor_check='odd 4x4 cells, native pixel center',
                              triple='source plus two same RGB/sensor voters; all pairs >= minimum angle'),
                  input_sha256={k: digest(getattr(args, k)) for k in ('anchors', 'analysis', 'consistency')},
                  samples_sha256=digest(args.samples/'samples.json'), groups_sha256=digest(args.groups/'groups.json'),
                  model_sha256=digest(args.work/'artifacts/gaussians.ply'),
                  sensor_evaluation_sha256=digest(args.sensor_evaluation) if sensors is not None else None,
                  variants=[])
    for variant in groups['variants']:
        mapping = checked_map(args.groups, variant)
        leaders, ids = mapping['leaders'], mapping['ids']
        if (not np.array_equal(mapping['source'], sources) or not np.array_equal(mapping['grid_index'], values['grid_index'])
                or leaders.dtype.kind not in 'iu' or ids.dtype.kind not in 'iu'
                or ids.shape != sources.shape or np.any(leaders < 0) or np.any(leaders >= len(sources))
                or len(np.unique(leaders)) != len(leaders) or np.any(ids < 0) or np.any(ids >= len(leaders))
                or not np.array_equal(ids[leaders], np.arange(len(leaders)))
                or not np.array_equal(leaders, [r['leader'] for r in variant['groups']])
                or not np.array_equal(np.bincount(ids, minlength=len(leaders)), [r['samples'] for r in variant['groups']])):
            raise ValueError('Group map identities differ')
        summary, arrays = audit(values['world'][leaders], sources[leaders], views, depths, config, sensors, scale, cloud)
        path = maps / variant['map']
        np.savez_compressed(path, leaders=leaders, **arrays)
        output['variants'].append(dict(radius=variant['radius'], order=variant['order'], summary=summary,
                                       map=path.name, map_sha256=digest(path)))
    output.update(status='complete', seconds=time.monotonic()-started)
    save(args.output, output)
    print(args.work.name, [(v['radius'], v['order'], v['summary']) for v in output['variants'] if v['radius'] == .01])
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('samples', 'groups', 'anchors', 'analysis', 'predictions', 'work', 'consistency', 'output'):
        parser.add_argument('--'+flag, type=Path, required=True)
    for flag in ('sensor-manifest', 'sensor-evaluation'):
        parser.add_argument('--'+flag, type=Path)
    run(parser.parse_args())
