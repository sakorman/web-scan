"""Test raw-RADIAL cross-view selection against frozen sensor check pixels.

Generate masks in a separate process with RGB/SfM priors only, then score them.
No depth correction, sensor-driven selection, point injection or training change.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from scripts.benchmark_depth_consistency import camera, run
from scripts.benchmark_depth_prior import digest, save
from scripts.benchmark_sensor_depth import score_depth


VARIANTS = ('all', 'supported', 'supported_no_front_conflict')


def radial_camera(view):
    # Import only in the geometry process, separately from PyTorch inference.
    import pycolmap

    params = np.asarray(view['camera_params'], dtype=float)
    if (view['camera_model'] != 'RADIAL' or params.shape != (5,)
            or not np.isfinite(params).all() or params[0] <= 0
            or view['width'] < 1 or view['height'] < 1):
        raise ValueError('Expected a valid raw RADIAL camera')
    _, pose = camera({**view, 'camera_model': 'PINHOLE',
                      'camera_params': [params[0], params[0], *params[1:3]]})
    return pycolmap.Camera(model='RADIAL', width=view['width'], height=view['height'], params=params), pose


def raw_project(points, calibration):
    lens, pose = calibration
    local = points @ pose[:, :3].T + pose[:, 3]
    xy = np.full((len(points), 2), np.nan)
    valid = np.isfinite(local).all(axis=1) & (local[:, 2] > 0)
    xy[valid] = lens.img_from_cam(local[valid])
    return xy, local[:, 2]


def raw_unproject(xy, z, calibration):
    lens, pose = calibration
    rays = np.full((len(xy), 2), np.nan)
    valid = np.isfinite(xy).all(axis=1) & np.isfinite(z) & (z > 0)
    rays[valid] = lens.cam_from_img(xy[valid])
    # Iterative undistortion must return the requested native pixel accurately.
    forward = lens.img_from_cam(np.column_stack([rays[valid], np.ones(valid.sum())]))
    residual = np.linalg.norm(forward - xy[valid], axis=1)
    rays[np.flatnonzero(valid)[~np.isfinite(residual) | (residual > 1e-5)]] = np.nan
    return (np.column_stack([rays, np.ones(len(xy))]) * z[:, None] - pose[:, 3]) @ pose[:, :3]


def selection_stats(prediction, reference, selected):
    prediction, reference, selected = np.asarray(prediction), np.asarray(reference), np.asarray(selected)
    if prediction.shape != reference.shape or selected.shape != prediction.shape or selected.dtype != bool:
        raise ValueError('Selection and depth shapes/types differ')
    valid = np.isfinite(reference) & (reference > 0)
    good = np.zeros(valid.shape, dtype=bool)
    usable = valid & np.isfinite(prediction) & (prediction > 0)
    good[usable] = np.abs(prediction[usable]/reference[usable] - 1) <= .1
    bad = valid & ~good  # Invalid predictions remain bad, not missing truth.
    ratio = lambda numerator, denominator: float(numerator/denominator) if denominator else None
    return {
        'check_positions': int(selected.size), 'selected_positions': int(selected.sum()),
        'coverage': ratio(selected.sum(), selected.size),
        'sensor_coverage': ratio((selected & valid).sum(), valid.sum()),
        'available_good': int(good.sum()), 'available_bad': int(bad.sum()),
        'selected_good': int((selected & good).sum()), 'selected_bad': int((selected & bad).sum()),
        'good_retention': ratio((selected & good).sum(), good.sum()),
        'bad_retention': ratio((selected & bad).sum(), bad.sum()),
        'retained': score_depth(prediction[selected], reference[selected]),
        'rejected': score_depth(prediction[~selected], reference[~selected]),
    }


def unique_rows(rows):
    result = {v['name']: v for v in rows}
    if len(result) != len(rows):
        raise ValueError('Duplicate view identity')
    return result


def evaluate(args):
    started = time.monotonic()
    cross = json.loads(args.consistency.read_text())
    sensor = json.loads(args.sensor.read_text())
    if cross['status'] != 'complete' or sensor['status'] != 'complete':
        raise ValueError('Incomplete diagnostics')
    if cross['analysis_sha256'] != sensor['analysis_sha256'] or len(cross['scenes']) != 1:
        raise ValueError('Diagnostics must share one aligned SfM scene')
    if cross['config']['grid_step'] != sensor['config']['grid_step']:
        raise ValueError('Diagnostic grids differ')
    source = unique_rows(cross['scenes'][0]['views'])
    truth = unique_rows(sensor['views'])
    if source.keys() != {name for name, row in truth.items() if row['sparse_screen_pass']}:
        raise ValueError('Selected view sets differ')
    args.output.mkdir(parents=True, exist_ok=False)
    result = {'status': 'running', 'scope': 'selection accuracy only; depth values unchanged',
              'consistency_sha256': digest(args.consistency), 'sensor_sha256': digest(args.sensor),
              'scale_m_per_sfm_unit': sensor['scale_m_per_sfm_unit'],
              'excluded_views': cross['scenes'][0]['excluded_views'], 'views': []}
    pooled = {variant: [[], [], []] for variant in VARIANTS}
    try:
        for name, row in source.items():
            sensor_row = truth[name]
            cp, sp = args.consistency.parent / row['map'], args.sensor.parent / sensor_row['map']
            if digest(cp) != row['map_sha256'] or digest(sp) != sensor_row['map_sha256']:
                raise ValueError('Map digest mismatch')
            with np.load(cp, allow_pickle=False) as c, np.load(sp, allow_pickle=False) as s:
                shape = tuple(row['grid_shape'])
                if (shape != tuple(sensor_row['grid_shape'])
                        or any(v.shape != shape for v in [*c.values(), *s.values()])):
                    raise ValueError('Map grid shapes differ')
                check = s['check']
                if check.dtype != bool or c['valid'].dtype != bool:
                    raise ValueError('Invalid grid masks')
                prediction, reference = s['prior_m'][check], s['sensor_m'][check]
                supported = c['valid'][check] & (c['support'][check] >= cross['config']['min_other_views'])
                masks = [np.ones(len(prediction), dtype=bool), supported, supported & (c['conflict'][check] == 0)]
                metrics = {}
                for variant, mask in zip(VARIANTS, masks):
                    metrics[variant] = selection_stats(prediction, reference, mask)
                    for bucket, values in zip(pooled[variant], (prediction, reference, mask)):
                        bucket.append(values)
                result['views'].append({'name': name, 'variants': metrics})
        result['pooled'] = {v: selection_stats(*(np.concatenate(a) for a in data)) for v, data in pooled.items()}
        result['equal_view'] = {}
        for variant in VARIANTS:
            rows = [v['variants'][variant] for v in result['views']]
            valid_rows = [v for v in rows if v['retained']['valid_prediction_metrics'] is not None]
            result['equal_view'][variant] = {
                'scorable_views': len(valid_rows), 'total_views': len(rows),
                'coverage': float(np.mean([v['coverage'] for v in rows])) if rows else None,
                'mean_abs_rel': float(np.mean([v['retained']['valid_prediction_metrics']['mean_abs_rel'] for v in valid_rows])) if valid_rows else None,
                'within_10_percent': float(np.mean([v['retained']['within_10_percent'] for v in valid_rows])) if valid_rows else None,
            }
        result['status'] = 'complete'
    except BaseException as error:
        result.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        result['seconds'] = time.monotonic() - started
        save(args.output / 'support-evaluation.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='stage', required=True)
    masks = subs.add_parser('masks')
    for flag in ('anchors', 'analysis', 'predictions', 'config', 'output'):
        masks.add_argument('--'+flag, type=Path, required=True)
    score = subs.add_parser('evaluate')
    for flag in ('consistency', 'sensor', 'output'):
        score.add_argument('--'+flag, type=Path, required=True)
    args = parser.parse_args()
    if args.stage == 'masks':
        run(args, camera_factory=radial_camera, project_fn=raw_project, unproject_fn=raw_unproject)
    else:
        evaluate(args)
