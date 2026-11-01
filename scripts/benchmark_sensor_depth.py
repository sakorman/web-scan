"""Check SfM-aligned relative depth against held-out TUM sensor pixels.

The RGB-only reconstruction is never modified. Sensor measurements set one
global scene scale using fit-region sparse anchors; check pixels never fit it.
Run preparation separately from PyTorch inference to isolate native runtimes.
"""
import argparse
import json
import tarfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.benchmark_depth_consistency import grid
from scripts.benchmark_depth_prior import camera_depth, digest, fit_cells, sample_depth, save


def associate(rgb_times, depth_times, max_seconds):
    """Greedy closest, one-to-one timestamp association; deterministic ties."""
    if not np.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError('Invalid timestamp tolerance')
    if (len(set(rgb_times)) != len(rgb_times) or len(set(depth_times)) != len(depth_times)
            or not np.isfinite([*rgb_times, *depth_times]).all()):
        raise ValueError('Duplicate or invalid timestamps')
    pairs = sorted((abs(r-d), i, j) for i, r in enumerate(rgb_times)
                   for j, d in enumerate(depth_times) if abs(r-d) <= max_seconds)
    used_rgb, used_depth, result = set(), set(), {}
    for delta, i, j in pairs:
        if i not in used_rgb and j not in used_depth:
            result[i] = (j, delta)
            used_rgb.add(i)
            used_depth.add(j)
    return result


def sensor_samples(depth, xy, factor=5000):
    """Nearest native pixel, no bilinear interpolation over holes or edges."""
    depth, xy = np.asarray(depth), np.asarray(xy, dtype=float).reshape(-1, 2)
    if depth.ndim != 2 or not np.isfinite(factor) or factor <= 0:
        raise ValueError('Invalid depth map or factor')
    h, w = depth.shape
    inside = np.isfinite(xy).all(axis=1) & (xy[:, 0] >= 0) & (xy[:, 0] < w)
    inside &= (xy[:, 1] >= 0) & (xy[:, 1] < h)
    result = np.full(len(xy), np.nan)
    x, y = np.floor(xy[inside]).astype(int).T
    result[inside] = depth[y, x] / factor
    valid = inside & np.isfinite(result) & (result > 0)
    result[~valid] = np.nan
    return result, valid


def fit_scene_scale(records):
    ratios = []
    counts = []
    for record in records:
        xy, z, sensor = record['xy'], record['z'], record['sensor']
        fit = fit_cells(xy, record['width'], record['height'])
        valid = fit & np.isfinite(z) & (z > 0) & np.isfinite(sensor) & (sensor > 0)
        ratios.extend((sensor[valid] / z[valid]).tolist())
        counts.append(int(valid.sum()))
    if len(ratios) < 20:
        raise ValueError('Insufficient fit-region sensor/SfM anchors')
    return float(np.median(ratios)), counts


def score_depth(prediction, reference):
    """Count invalid predictions in the denominator, never silently drop them."""
    prediction, reference = np.asarray(prediction), np.asarray(reference)
    if prediction.shape != reference.shape:
        raise ValueError('Depth sample shapes differ')
    valid_reference = np.isfinite(reference) & (reference > 0)
    valid = valid_reference & np.isfinite(prediction) & (prediction > 0)
    count = int(valid_reference.sum())
    errors = np.abs(prediction[valid] / reference[valid] - 1)
    row = {'reference_samples': count, 'missing_reference': int((~valid_reference).sum()),
           'invalid_predictions': int((valid_reference & ~valid).sum())}
    if not count or not valid.any():
        return {**row, 'valid_prediction_metrics': None, 'within_10_percent': 0.0,
                'delta_1_25': 0.0, 'passed': False}
    ratio = np.maximum(prediction[valid] / reference[valid], reference[valid] / prediction[valid])
    row.update(valid_prediction_metrics={'median_abs_rel': float(np.median(errors)),
               'p90_abs_rel': float(np.percentile(errors, 90)), 'mean_abs_rel': float(errors.mean()),
               'rmse_m': float(np.sqrt(np.mean((prediction[valid]-reference[valid])**2)))},
               within_10_percent=float((errors <= .1).sum()/count),
               delta_1_25=float((ratio < 1.25).sum()/count))
    row['passed'] = bool(row['invalid_predictions'] == 0 and np.median(errors) <= .1
                         and np.percentile(errors, 90) <= .25)
    return row


def prepare(args):
    import pycolmap

    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text())
    result = json.loads((args.work / 'result.json').read_text())
    assert result['status'] == 'complete' and result['report']['evaluation_verified']
    holdout = {Path(v['name']).stem for v in result['report']['evaluation_images']}
    model = pycolmap.Reconstruction(args.work / 'sfm/chosen')
    by_name = {im.name: im for im in model.images.values()}
    inputs = [result['inputs'][i] for i in config['probe_indices']]
    assert len({v['name'] for v in inputs}) == len(inputs)
    assert not {Path(v['name']).stem for v in inputs} & holdout
    views, measurements = [], []
    with tarfile.open(args.archive) as archive:
        prefix = 'rgbd_dataset_freiburg1_desk/'
        depth_list = archive.extractfile(prefix + 'depth.txt').read()
        entries = [line.split() for line in depth_list.decode().splitlines()
                   if line.strip() and not line.startswith('#')]
        associations = associate([float(v['timestamp']) for v in inputs],
                                 [float(v[0]) for v in entries], config['association_max_seconds'])
        unmatched = [{'name': record['name'], 'rgb_timestamp': record['timestamp'],
                      'reason': 'no_unique_depth_within_tolerance',
                      'nearest_seconds': min(abs(float(record['timestamp'])-float(v[0])) for v in entries)}
                     for i, record in enumerate(inputs) if i not in associations]
        if not associations:
            raise ValueError('No selected RGB frame has a depth match within tolerance')
        for i, record in enumerate(inputs):
            if i not in associations:
                continue
            im = by_name[record['name']]
            camera = model.cameras[im.camera_id]
            image = args.work / 'input' / im.name
            assert digest(image) == record['sha256']
            pose = np.asarray(im.cam_from_world().matrix())
            xy, z = [], []
            for obs in im.points2D:
                if not obs.has_point3D():
                    continue
                point = model.points3D[obs.point3D_id]
                value = camera_depth(pose, point.xyz)
                if point.track.length() >= 2 and np.isfinite(point.error) and point.error < 4 and value > 0:
                    xy.append(obs.xy.tolist()); z.append(value)
            views.append({'job': args.work.name, 'name': im.name, 'image': str(image.resolve()),
                          'image_sha256': record['sha256'], 'width': camera.width, 'height': camera.height,
                          'camera_model': camera.model.name, 'camera_params': camera.params.tolist(),
                          'cam_from_world': pose.tolist(), 'xy': xy, 'z': z})
            index, delta = associations[i]
            timestamp, member = entries[index]
            target = args.output / f'{i:04d}-depth.png'
            target.write_bytes(archive.extractfile(prefix + member).read())
            with Image.open(target) as depth:
                assert depth.size == (camera.width, camera.height) and depth.mode in ('I;16', 'I')
            measurements.append({'name': im.name, 'rgb_timestamp': record['timestamp'],
                                 'depth_timestamp': timestamp, 'time_delta_seconds': delta,
                                 'member': prefix+member, 'depth': target.name, 'depth_sha256': digest(target)})
    save(args.output / 'anchors.json', {'views': views, 'domain': 'raw RGB; no sensor resampling'})
    save(args.output / 'sensor.json', {'archive_sha256': digest(args.archive),
         'archive_bytes': args.archive.stat().st_size, 'config_sha256': digest(args.config),
         'anchor_sha256': digest(args.output / 'anchors.json'), 'views': measurements,
         'selected_rgb_count': len(inputs), 'unmatched': unmatched})


def evaluate(args):
    started = time.monotonic()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text())
    if config['gate'] != {'median_abs_rel': .1, 'p90_abs_rel': .25}:
        raise ValueError('This diagnostic uses the fixed 10% / 25% screen')
    manifest = json.loads(args.manifest.read_text())
    analysis = json.loads(args.analysis.read_text())
    anchors = json.loads(args.anchors.read_text())['views']
    if len({v['job'] for v in anchors}) != 1:
        raise ValueError('One scene scale requires exactly one SfM reconstruction')
    assert digest(args.config) == manifest['config_sha256']
    assert digest(args.anchors) == manifest['anchor_sha256'] == analysis['anchor_sha256']
    assert analysis['inference']['anchor_sha256'] == manifest['anchor_sha256']
    assert analysis['inference']['status'] == 'complete'
    if config.get('weight_sha256'):
        assert analysis['inference']['weights_sha256'] == config['weight_sha256']
    rows = {v['name']: v for v in analysis['views']}
    pred = {v['name']: v for v in analysis['inference']['views']}
    sensor = {v['name']: v for v in manifest['views']}
    assert len(anchors) == len(rows) == len(pred) == len(sensor)
    assert {v['name'] for v in anchors} == rows.keys() == pred.keys() == sensor.keys()
    records = []
    for view in anchors:
        s = sensor[view['name']]
        path = args.manifest.parent / s['depth']
        assert digest(path) == s['depth_sha256']
        assert digest(view['image']) == view['image_sha256'] == pred[view['name']]['image_sha256']
        depth = np.asarray(Image.open(path))
        assert depth.shape == (view['height'], view['width'])
        xy = np.asarray(view['xy']).reshape(-1, 2)
        samples, _ = sensor_samples(depth, xy, config['depth_factor'])
        records.append({**view, 'xy': xy, 'z': np.asarray(view['z']), 'sensor': samples, 'depth': depth})
    scale, counts = fit_scene_scale(records)
    output = {'status': 'running', 'scale_m_per_sfm_unit': scale, 'fit_anchor_counts': counts,
              'config': config, 'manifest_sha256': digest(args.manifest),
              'analysis_sha256': digest(args.analysis), 'views': []}
    try:
        for view in records:
            name = view['name']; alignment = rows[name]
            path = args.predictions / pred[name]['prediction']
            assert digest(path) == pred[name]['prediction_sha256']
            prediction = np.load(path, allow_pickle=False)
            assert prediction.shape == view['depth'].shape
            xy, shape = grid(view['width'], view['height'], config['grid_step'])
            check = ~fit_cells(xy, view['width'], view['height'])
            reference, _ = sensor_samples(view['depth'], xy, config['depth_factor'])
            relative, _ = sample_depth(prediction, xy)
            fitted = np.full(len(xy), np.nan)
            if alignment.get('scale', 0) > 0:
                inverse = alignment['scale']*relative + alignment['shift']
                np.divide(scale, inverse, out=fitted, where=np.isfinite(inverse) & (inverse > 0))
            fit = fit_cells(view['xy'], view['width'], view['height'])
            constant = np.full(len(xy), np.median(view['z'][fit])*scale)
            row = {'name': name, 'sparse_screen_pass': alignment['passed'],
                   'sparse_screen_reason': alignment['reason'], 'grid_shape': list(shape),
                   'prior': score_depth(fitted[check], reference[check]),
                   'constant': score_depth(constant[check], reference[check]),
                   'sfm_check_anchors': score_depth(view['z'][~fit]*scale, view['sensor'][~fit])}
            map_name = Path(name).stem + '.npz'
            np.savez_compressed(args.output / map_name, prior_m=fitted.reshape(shape),
                                sensor_m=reference.reshape(shape), check=check.reshape(shape))
            row.update(map=map_name, map_sha256=digest(args.output/map_name))
            output['views'].append(row)
        output['status'] = 'complete'
    except BaseException as error:
        output.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        output['seconds'] = time.monotonic() - started
        save(args.output / 'sensor-evaluation.json', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='stage', required=True)
    prep = subs.add_parser('prepare')
    for flag in ('work', 'archive', 'config', 'output'):
        prep.add_argument('--'+flag, type=Path, required=True)
    score = subs.add_parser('evaluate')
    for flag in ('anchors', 'predictions', 'analysis', 'manifest', 'config', 'output'):
        score.add_argument('--'+flag, type=Path, required=True)
    args = parser.parse_args()
    {'prepare': prepare, 'evaluate': evaluate}[args.stage](args)
