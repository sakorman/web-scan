"""Audit reciprocal GMDepth estimates without changing Gaussian initialization.

Preparation freezes reverse pairs. Audit uses RGB/SfM only; sensor scoring is a
separate step and never changes the masks. Coordinates use COLMAP pixel corners.
"""
import argparse
import copy
import json
from pathlib import Path
import time

import numpy as np

from scripts.benchmark_depth_consistency import camera, check_pair, grid
from scripts.benchmark_depth_prior import digest, fit_cells, sample_depth, save
from scripts.benchmark_gmdepth import network_intrinsics, relative_pose, UPSTREAM, WEIGHTS_SHA256
from scripts.benchmark_sensor_depth import score_depth


def read(path):
    return json.loads(Path(path).read_text())


def reverse_pair(row):
    result = copy.deepcopy(row)
    source, target = result['target'], result['source']
    values = np.asarray(source['z'])[fit_cells(source['xy'], *source['size'])]
    if len(values) < 20 or not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError('Insufficient valid reverse fit anchors')
    near, far = np.quantile(values, [.01, .99]) * [.5, 2]
    result.update(source=source, target=target, near=float(near), far=float(far),
                  K=network_intrinsics(source['K'], source['size'], row['size']).tolist(),
                  pose=relative_pose(source['pose'], target['pose']).tolist())
    return result


def calibration(view, size):
    k = network_intrinsics(view['K'], view['size'], size)
    # The shared geometric helper samples at corner coordinates, unlike UniMatch.
    return camera(dict(camera_model='PINHOLE', camera_params=[k[0, 0], k[1, 1], k[0, 2]+.5, k[1, 2]+.5],
                       cam_from_world=np.asarray(view['pose'])[:3]))


def validate_pairs(path):
    pairs = read(path)
    work = Path(pairs['work'])
    if pairs['status'] != 'complete' or digest(work/'result.json') != pairs['result_sha256']:
        raise ValueError('Reconstruction result changed')
    if digest(work/'artifacts/gaussians.ply') != pairs['model_sha256']:
        raise ValueError('Reconstruction model changed')
    actual = {p.name: digest(p) for p in (work/'sfm/undistorted/sparse').glob('*.bin')}
    if actual != pairs['sparse_sha256']:
        raise ValueError('Sparse cameras or geometry changed')
    held = {Path(v['name']).stem for v in read(work/'result.json')['report']['evaluation_images']}
    for row in pairs['records']:
        for view in (row['source'], row['target']):
            if Path(view['name']).stem in held or digest(view['image']) != view['sha256']:
                raise ValueError('Pair identity changed or contains a holdout')
    return pairs


def load_prediction(directory, pairs_path, name, size):
    metadata = read(directory/'inference.json')
    if (metadata['status'] != 'complete' or metadata['pairs_sha256'] != digest(pairs_path)
            or metadata['upstream'] != UPSTREAM or metadata['weights_sha256'] != WEIGHTS_SHA256):
        raise ValueError('Prediction provenance changed')
    matches = [v for v in metadata['views'] if v['name'] == name]
    if len(matches) != 1:
        raise ValueError('Missing or duplicate prediction')
    row = matches[0]
    path = directory/row['depth']
    if digest(path) != row['sha256']:
        raise ValueError('Prediction bytes changed')
    depth = np.load(path, allow_pickle=False)
    if depth.shape != tuple(size[::-1]) or not np.isfinite(depth).all() or np.any(depth <= 0):
        raise ValueError('Invalid prediction array')
    return depth, metadata


def prepare(args):
    pairs = validate_pairs(args.pairs)
    args.output.mkdir(parents=True, exist_ok=False)
    index = dict(forward_pairs_sha256=digest(args.pairs), records=[])
    # Each pair gets a directory: the same target may occur with different sources.
    for i, row in enumerate(pairs['records']):
        sub = args.output/f'{i:02d}'
        sub.mkdir()
        reverse = {**pairs, 'records': [reverse_pair(row)], 'selection': 'reverse frozen forward pair'}
        save(sub/'pairs.json', reverse)
        index['records'].append(dict(source=row['source']['name'], target=row['target']['name'],
                                     directory=sub.name, pairs_sha256=digest(sub/'pairs.json')))
    save(args.output/'index.json', index)


def audit(args):
    started = time.monotonic()
    pairs = validate_pairs(args.pairs)
    reverse = read(args.reverse/'index.json')
    assert reverse['forward_pairs_sha256'] == digest(args.pairs)
    assert len(reverse['records']) == len(pairs['records'])
    config = read(args.config)
    if (not 0 < config['relative_depth_tolerance'] < 1 or config['roundtrip_pixels'] < 0
            or config['min_parallax_degrees'] < 0 or config['grid_step'] < 1):
        raise ValueError('Invalid audit thresholds')
    raw = None
    if args.raw_anchors:
        import pycolmap
        raw = {Path(v['name']).stem: v for v in read(args.raw_anchors)['views']}
    args.output.mkdir(parents=True, exist_ok=False)
    out = dict(config=config, forward_pairs_sha256=digest(args.pairs),
               reverse_index_sha256=digest(args.reverse/'index.json'), views=[])
    if raw is not None:
        out['raw_anchors_sha256'] = digest(args.raw_anchors)
    for row, rev in zip(pairs['records'], reverse['records'], strict=True):
        source = row['source']; name = source['name']; key = Path(name).stem
        assert (name, row['target']['name']) == (rev['source'], rev['target'])
        rev_path = args.reverse/rev['directory']/'pairs.json'
        assert digest(rev_path) == rev['pairs_sha256']
        assert read(rev_path)['records'] == [reverse_pair(row)]
        forward, _ = load_prediction(args.predictions, args.pairs, name, row['size'])
        backward, inf = load_prediction(args.reverse/rev['directory']/'inference', rev_path,
                                        row['target']['name'], row['size'])
        if raw is None:
            xy, shape = grid(*source['size'], config['grid_step'])
            domain_xy = xy.copy()
            xy *= np.asarray(row['size'])/source['size']
            check = ~fit_cells(domain_xy, *source['size'])
        else:
            v = raw[key]
            assert digest(v['image']) == v['image_sha256']
            assert np.allclose(v['cam_from_world'], np.asarray(source['pose'])[:3])
            raw_camera = pycolmap.Camera(model=v['camera_model'], width=v['width'], height=v['height'], params=v['camera_params'])
            domain_xy, shape = grid(v['width'], v['height'], config['grid_step'])
            rays = raw_camera.cam_from_img(domain_xy)
            xy = (np.c_[rays, np.ones(len(rays))] @ np.asarray(source['K']).T)[:, :2]
            xy *= np.asarray(row['size'])/source['size']
            check = ~fit_cells(domain_xy, v['width'], v['height'])
        z, inside = sample_depth(forward, xy)
        masks = check_pair(xy, z, calibration(source, row['size']),
                           calibration(row['target'], row['size']), backward, config)
        path = args.output/(key+'.npz')
        np.savez_compressed(path, **{k: v.reshape(shape) for k, v in masks.items()},
                            depth=z.reshape(shape), check=(check & inside).reshape(shape))
        out['views'].append(dict(name=name, target=row['target']['name'], map=path.name,
            map_sha256=digest(path), counts={k: int((v & check & inside).sum()) for k, v in masks.items()},
            check_samples=int((check & inside).sum()), outside_source=int((check & ~inside).sum()),
            reverse_inference=inf, reverse_pairs_sha256=digest(rev_path)))
    out.update(status='complete', seconds=time.monotonic()-started)
    save(args.output/'audit.json', out)


def score_regions(gmdepth, mono, reference, check, support):
    result = {}
    for key, mask in [('all', check), ('retained', check & support), ('rejected', check & ~support)]:
        result[key] = dict(grid_samples=int(mask.sum()), gmdepth=score_depth(gmdepth[mask], reference[mask]),
                           monocular=score_depth(mono[mask], reference[mask]))
    return result


def assert_scores_close(actual, expected, *, atol=1e-12):
    """Compare frozen metrics while allowing JSON/NumPy reduction roundoff."""
    if actual.keys() != expected.keys():
        raise AssertionError((actual.keys(), expected.keys()))
    for key in actual:
        if isinstance(actual[key], dict):
            assert_scores_close(actual[key], expected[key], atol=atol)
        elif isinstance(actual[key], float):
            if not np.isclose(actual[key], expected[key], rtol=1e-12, atol=atol):
                raise AssertionError((key, actual[key], expected[key]))
        elif actual[key] != expected[key]:
            raise AssertionError((key, actual[key], expected[key]))


def score(args):
    started = time.monotonic()
    audit_meta = read(args.audit/'audit.json')
    baseline = read(args.baseline/'scores.json')
    evaluation = read(args.sensor_root/'evaluation/sensor-evaluation.json')
    manifest = read(args.sensor_root/'sensor/sensor.json')
    assert audit_meta['status'] == baseline['status'] == evaluation['status'] == 'complete'
    assert audit_meta['forward_pairs_sha256'] == baseline['pairs_sha256']
    assert audit_meta['raw_anchors_sha256'] == manifest['anchor_sha256'] == digest(args.sensor_root/'sensor/anchors.json')
    assert evaluation['manifest_sha256'] == digest(args.sensor_root/'sensor/sensor.json')
    assert baseline['sensor_evaluation_sha256'] == digest(args.sensor_root/'evaluation/sensor-evaluation.json')
    refs = {Path(v['name']).stem: v for v in evaluation['views']}
    old_rows = {v['name']: v for v in baseline['views']}
    args.output.mkdir(parents=True, exist_ok=False)
    out = dict(audit_sha256=digest(args.audit/'audit.json'), baseline_sha256=digest(args.baseline/'scores.json'), views=[])
    for row in audit_meta['views']:
        key = Path(row['name']).stem
        path = args.audit/row['map']; assert digest(path) == row['map_sha256']
        reference_path = args.sensor_root/'evaluation'/refs[key]['map']
        assert digest(reference_path) == refs[key]['map_sha256']
        with np.load(path, allow_pickle=False) as data, np.load(reference_path, allow_pickle=False) as truth:
            check = data['check']
            assert not np.any(check & ~truth['check'])
            scores = score_regions(data['depth']*evaluation['scale_m_per_sfm_unit'], truth['prior_m'],
                                   truth['sensor_m'], check, data['supported'])
        # Baseline must be exactly reproduced, including the denominator.
        for method in ('gmdepth', 'monocular'):
            assert_scores_close(scores['all'][method], old_rows[row['name']]['sensor'][method])
        out['views'].append(dict(name=row['name'], regions=scores))
    out.update(status='complete', seconds=time.monotonic()-started)
    save(args.output/'scores.json', out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='stage', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--pairs', type=Path, required=True)
    p = sub.add_parser('audit')
    for name in ('pairs', 'predictions', 'reverse', 'config'): p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--raw-anchors', type=Path)
    p = sub.add_parser('score')
    for name in ('audit', 'baseline', 'sensor-root'): p.add_argument('--'+name, type=Path, required=True)
    for p in sub.choices.values(): p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); globals()[args.stage](args)
