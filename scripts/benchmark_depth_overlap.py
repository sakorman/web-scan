"""Measure whether supported depth samples lie outside existing sparse tracks.

Distances are relative to image diagonal (2D) and local camera Z (3D).
Sensor measurements only audit selected samples; nothing is added to a model.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from scripts.benchmark_depth_consistency import camera, grid, unproject
from scripts.benchmark_depth_prior import digest, fit_cells, save
from scripts.benchmark_depth_support import radial_camera, raw_unproject, unique_rows


FRACTIONS = (0.005, 0.01, 0.02)


def nearest(query, reference):
    """Exact Euclidean distance; block to bound the temporary pair matrices."""
    if not len(reference) or not np.isfinite(reference).all() or not np.isfinite(query).all():
        raise ValueError('Nearest distance requires a finite cloud and queries')
    output = np.empty(len(query))
    block = max(1, 250_000 // len(reference))
    for start in range(0, len(query), block):
        q = query[start:start + block]
        d2 = (q[:, None, 0] - reference[:, 0]) ** 2
        for axis in range(1, q.shape[1]):
            d2 += (q[:, None, axis] - reference[:, axis]) ** 2
        output[start:start + len(q)] = np.sqrt(d2.min(axis=1))
    return output


def classify(t, image_distance, space_distance, conflict, sensor_distance=None, relative_error=None, displacement=None):
    """All arrays refer to the same supported subset, never treat missing truth as agreement."""
    far_image, far_space = image_distance > t, space_distance > t
    both = far_image & far_space
    clean = both & ~conflict
    counts = {'supported': len(both), 'image_far': int(far_image.sum()), 'space_far': int(far_space.sum()),
              'both_far': int(both.sum()), 'both_far_no_front_conflict': int(clean.sum())}
    if sensor_distance is not None:
        valid = np.isfinite(sensor_distance)
        for label, subset in [('both', both), ('clean', clean)]:
            corroborated = subset & valid & (sensor_distance > t) & (relative_error <= .1)
            counts[label + '_sensor_valid'] = int((subset & valid).sum())
            counts[label + '_sensor_far_and_within_10pct'] = int(corroborated.sum())
            counts[label + '_within_spatial_radius'] = int((corroborated & (displacement <= t)).sum())
    return counts


def read(path):
    return json.loads(path.read_text())


def checked_map(directory, row):
    path = directory / row['map']
    if digest(path) != row['map_sha256']:
        raise ValueError('Map identity mismatch')
    with np.load(path, allow_pickle=False) as data:
        return dict(data)


def observations(model, im, view):
    lens = model.cameras[im.camera_id]
    pose = im.cam_from_world().matrix()
    if (im.name != view['name'] or lens.model.name != view['camera_model']
            or (lens.width, lens.height) != (view['width'], view['height'])
            or not np.allclose(pose, view['cam_from_world'], rtol=0, atol=1e-12)
            or not np.array_equal(lens.params, view['camera_params'])):
        raise ValueError('Camera/pose differs from frozen reconstruction')
    # Use ALL linked observations for nearness, including ones not used to fit the prior.
    xy, anchor_xy, anchor_z = [], [], []
    for obs in im.points2D:
        if obs.has_point3D():
            point = model.points3D[obs.point3D_id]
            xy.append(obs.xy)
            z = (pose[:, :3] @ point.xyz + pose[:, 3])[2]
            if point.track.length() >= 2 and np.isfinite(point.error) and point.error < 4 and z > 0:
                anchor_xy.append(obs.xy)
                anchor_z.append(z)
    if not (np.array_equal(anchor_xy, view['xy']) and np.allclose(anchor_z, view['z'], rtol=1e-12, atol=1e-12)):
        raise ValueError('Frozen sparse observations or depths differ')
    return np.asarray(xy)


def measure(view, row, cross_dir, cfg, analysis, predictions, xy_ref, cloud, sensor_row, sensor_dir, scale):
    w, h = view['width'], view['height']
    xy, shape = grid(w, h, cfg['grid_step'])
    c = checked_map(cross_dir, row)
    if (list(shape) != row['grid_shape'] or any(a.shape != shape for a in c.values())
            or c['valid'].dtype != bool or digest(view['image']) != view['image_sha256']):
        raise ValueError('Source image or support grid differs')
    valid = c['valid'].ravel()
    retained = valid & (c['support'].ravel() >= cfg['min_other_views'])
    if retained.sum() != row['retained_samples']:
        raise ValueError('Frozen support count differs')

    # Native grid uses exact pixel centers; no interpolation needed at these coordinates.
    pred = predictions[view['name']]
    pp = pred['path']
    if digest(pp) != pred['prediction_sha256'] or pred['image_sha256'] != view['image_sha256']:
        raise ValueError('Prior/photograph identity mismatch')
    raw = np.load(pp, allow_pickle=False)
    if raw.shape != (h, w):
        raise ValueError('Prior dimensions differ from camera')
    x, y = xy.astype(int).T
    inv = analysis['scale'] * raw[y, x].astype(float) + analysis['shift']
    if not np.array_equal(np.isfinite(inv) & (inv > 0), valid):
        raise ValueError('Valid prior mask differs')
    scope = valid.copy()
    if sensor_row is not None:
        s = checked_map(sensor_dir, sensor_row)
        if (any(a.shape != shape for a in s.values()) or s['check'].dtype != bool
                or not np.array_equal(s['check'].ravel(), ~fit_cells(xy, w, h))):
            raise ValueError('Sensor grid differs')
        scope &= s['check'].ravel()
    keep = scope & retained
    image_dist = nearest(xy, xy_ref) / np.hypot(w, h)
    z = 1 / inv[keep]
    if view['camera_model'] == 'RADIAL':
        calibration, lift = radial_camera(view), raw_unproject
    else:
        calibration, lift = camera(view), unproject
    world = lift(xy[keep], z, calibration)
    space_dist = nearest(world, cloud) / z
    measured_dist = error = displacement = None
    if sensor_row is not None:
        reference = s['sensor_m'].ravel()[keep]
        if not np.allclose(z * scale, s['prior_m'].ravel()[keep], rtol=1e-10, atol=1e-10):
            raise ValueError('Sensor prior depths differ')
        good = np.isfinite(reference) & (reference > 0)
        measured_dist = np.full(len(z), np.nan)
        error = np.full(len(z), np.nan)
        true_z = reference[good] / scale
        measured_world = lift(xy[keep][good], true_z, calibration)
        measured_dist[good] = nearest(measured_world, cloud) / true_z
        displacement = np.full(len(z), np.nan)
        displacement[good] = np.linalg.norm(world[good] - measured_world, axis=1) / true_z
        error[good] = np.abs(z[good] / true_z - 1)
    levels = {}
    for t in FRACTIONS:
        counts = classify(t, image_dist[keep], space_dist, c['conflict'].ravel()[keep] > 0, measured_dist, error, displacement)
        counts.update(eligible=int(scope.sum()), eligible_image_far=int((scope & (image_dist > t)).sum()))
        levels[str(t)] = counts
    return {'name': view['name'], 'linked_observations': len(xy_ref), 'levels': levels}


def run(args):
    import pycolmap

    start = time.monotonic()
    cross, analysis = read(args.consistency), read(args.analysis)
    if (cross['status'] != 'complete' or cross['analysis_sha256'] != digest(args.analysis)
            or cross['anchors_sha256'] != digest(args.anchors) or analysis['anchor_sha256'] != digest(args.anchors)):
        raise ValueError('Anchor, alignment and consistency identities differ')
    scenes = {v['job']: v for v in cross['scenes']}
    scene = scenes[args.job.name]
    select = lambda rows: unique_rows([v for v in rows if v['job'] == args.job.name])
    views = select(read(args.anchors)['views'])
    align = select(analysis['views'])
    predictions = select(analysis['inference']['views'])
    for pred in predictions.values():
        pred['path'] = args.predictions / pred['prediction']
    rows = unique_rows(scene['views'])
    if not rows:
        raise ValueError('No selected training views to assess')
    if rows.keys() != {n for n, v in align.items() if v['passed']}:
        raise ValueError('Selected view set differs')
    result = read(args.job / 'result.json')
    holdout = {Path(v['name']).stem for v in result['report']['evaluation_images']}
    if (result['status'] != 'complete' or not result['report']['evaluation_verified']
            or any(Path(n).stem in holdout for n in rows)):
        raise ValueError('Expected training views of a verified real reconstruction')
    sensors, scale = {}, None
    if args.sensor:
        sensor = read(args.sensor)
        if (sensor['status'] != 'complete' or sensor['analysis_sha256'] != cross['analysis_sha256']
                or sensor['config']['grid_step'] != cross['config']['grid_step'] or len(scenes) != 1):
            raise ValueError('Sensor and cross-view diagnostics differ')
        scale = sensor['scale_m_per_sfm_unit']
        sensors = unique_rows(sensor['views'])
        if scale <= 0 or not np.isfinite(scale) or rows.keys() != {n for n, v in sensors.items() if v['sparse_screen_pass']}:
            raise ValueError('Invalid sensor scale or view set')
    kinds = {views[n]['camera_model'] for n in rows}
    if kinds not in ({'RADIAL'}, {'PINHOLE'}):
        raise ValueError('Expected one supported camera domain')
    sparse = args.job / ('sfm/chosen' if kinds == {'RADIAL'} else 'sfm/undistorted/sparse')
    model = pycolmap.Reconstruction(sparse)
    training_sparse = args.job / 'sfm/undistorted/sparse'
    if sparse != training_sparse:
        # A change to the undistorted cloud would invalidate raw-domain overlap as an
        # initialization diagnostic, even if poses and the visible tracks still match.
        seed = pycolmap.Reconstruction(training_sparse).points3D
        raw = model.points3D
        if set(seed) != set(raw) or any(not np.array_equal(raw[i].xyz, seed[i].xyz) for i in seed):
            raise ValueError('Raw and training sparse point clouds differ')
    images = {im.name: im for im in model.images.values()}
    cloud = np.array([p.xyz for p in model.points3D.values()])
    stats = []
    for n, r in rows.items():
        linked = observations(model, images[n], views[n])
        stats.append(measure(views[n], r, args.consistency.parent, cross['config'], align[n], predictions,
                             linked, cloud, sensors.get(n), args.sensor.parent if args.sensor else None, scale))
    pooled = {t: {key: sum(v['levels'][t][key] for v in stats) for key in counts}
              for t, counts in stats[0]['levels'].items()}
    output = {'job': args.job.name, 'camera_domain': next(iter(kinds)), 'sparse_points': len(cloud),
              'grid_step': cross['config']['grid_step'], 'result_sha256': digest(args.job/'result.json'),
              'sparse_sha256': {p.name: digest(p) for p in sorted(sparse.glob('*.bin'))},
              'training_points_sha256': digest(training_sparse / 'points3D.bin'),
              'consistency_sha256': digest(args.consistency), 'sensor_sha256': digest(args.sensor) if args.sensor else None,
              'scale_m_per_sfm_unit': scale, 'views': stats, 'pooled': pooled, 'seconds': time.monotonic() - start}
    with args.output.open('x') as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('job', 'anchors', 'analysis', 'predictions', 'consistency', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--sensor', type=Path)
    run(parser.parse_args())
