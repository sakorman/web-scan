"""Score both frozen reverse depth estimates on all sensor-matched target cameras."""
import argparse
from collections import defaultdict
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image

from scripts.benchmark_depth_consistency import grid
from scripts.benchmark_depth_prior import digest, fit_cells, sample_depth, save
from scripts.benchmark_gmdepth_bidir import frozen_reverse
from scripts.benchmark_gmdepth_bidir_score import load_map
from scripts.benchmark_gmdepth_cycle import assert_scores_close, read, validate_pairs
from scripts.benchmark_sensor_depth import prepare, score_depth, sensor_samples


def target_indices(pairs, inputs):
    """Resolve the exact distinct target identities; never infer indices from filenames."""
    names = [v['name'] for v in inputs]
    targets = {r['target']['name'] for r in pairs}
    if not targets or len(names) != len(set(names)) or not targets.issubset(names):
        raise ValueError('Missing or ambiguous target identity')
    return [i for i, name in enumerate(names) if name in targets]


def aggregate(rows):
    """First average repeats of a target, then give every target one vote."""
    groups = defaultdict(list)
    for row in rows:
        if row['status'] == 'scored':
            values = [row[k]['valid_prediction_metrics'] for k in ('shared', 'independent')]
            if not all(values):
                raise ValueError('No valid sensor samples for matched target')
            groups[row['target']].append([v['mean_abs_rel'] for v in values])
    if not groups:
        return {'targets': 0, 'pairs': 0}
    means = np.array([np.mean(v, axis=0) for v in groups.values()])
    return dict(targets=len(groups), pairs=sum(map(len,groups.values())),
                shared=float(means[:,0].mean()), independent=float(means[:,1].mean()),
                improved=int((means[:,1] < means[:,0]).sum()), tied=int((means[:,1] == means[:,0]).sum()))


def score_pair(row, both, reverse, anchor, truth, config, scale):
    import pycolmap
    v = row['target']
    if not np.allclose(anchor['cam_from_world'], np.asarray(v['pose'])[:3]) or v['name'] != anchor['name']:
        raise ValueError('Target camera identity changed')
    cam = pycolmap.Camera(model=anchor['camera_model'], width=anchor['width'], height=anchor['height'],
                          params=anchor['camera_params'])
    xy, _ = grid(cam.width, cam.height, config['grid_step'])
    raw, _ = sensor_samples(truth, xy, config['depth_factor'])
    rays = cam.cam_from_img(xy)
    uv = (np.c_[rays,np.ones(len(rays))] @ np.asarray(v['K']).T)[:,:2] * np.asarray(row['size'])/v['size']
    a, inside = sample_depth(both[1], uv)
    b, inside_b = sample_depth(reverse, uv)
    check = ~fit_cells(xy, cam.width, cam.height)
    if not np.array_equal(inside, inside_b):
        raise ValueError('Reverse maps have different coverage')
    mask = check & inside
    return dict(grid_samples=int(mask.sum()), outside_network=int((check & ~inside).sum()),
                shared=score_depth(a[mask]*scale, raw[mask]), independent=score_depth(b[mask]*scale, raw[mask]))


def run(args):
    started = time.monotonic()
    pairs = validate_pairs(args.pairs)
    work = Path(pairs['work'])
    result = read(work/'result.json')
    meta = read(args.predictions/'bidir.json')
    index = read(args.reverse/'index.json')
    old = read(args.old_score)
    root = args.sensor_root
    evaluation = read(root/'evaluation/sensor-evaluation.json')
    manifest = read(root/'sensor/sensor.json')
    if (not all(v['status']=='complete' for v in (meta, old, evaluation)) or
            digest(args.pairs) != meta['pairs_sha256'] or meta['pairs_sha256'] != index['forward_pairs_sha256'] or
            meta['reverse_index_sha256'] != digest(args.reverse/'index.json') or
            old['prediction_sha256'] != digest(args.predictions/'bidir.json') or
            old['evaluation_sha256'] != digest(root/'evaluation/sensor-evaluation.json') or
            evaluation['manifest_sha256'] != digest(root/'sensor/sensor.json') or
            manifest['archive_sha256'] != digest(args.archive) or work != (root/'jobs/tum-baseline').resolve()):
        raise ValueError('Frozen prediction, reconstruction or sensor references changed')
    args.output.mkdir(parents=True, exist_ok=False)
    config = {**evaluation['config'], 'probe_indices': target_indices(pairs['records'], result['inputs'])}
    save(args.output/'config.json', config)
    prepared = args.output/'sensor'
    prepare(SimpleNamespace(output=prepared, config=args.output/'config.json', work=work, archive=args.archive))
    references = read(prepared/'sensor.json')
    anchors = {v['name']:v for v in read(prepared/'anchors.json')['views']}
    depths = {v['name']:v for v in references['views']}
    missing = {v['name']:v for v in references['unmatched']}
    original = {v['name']:v for v in manifest['views']}
    scale = evaluation['scale_m_per_sfm_unit']
    rows = []
    for pair, saved, rev, prior in zip(pairs['records'], meta['views'], index['records'], old['views'], strict=True):
        target = pair['target']['name']
        if (prior['source']['name'] != pair['source']['name'] or
                (prior['target'] is not None and prior['target']['name'] != target)):
            raise ValueError('Frozen sensor pair identity changed')
        record = dict(source=pair['source']['name'], target=target, previously_scored=prior['target'] is not None)
        if target not in depths:
            if record['previously_scored']:
                raise ValueError('Previously matched depth is missing')
            rows.append({**record, 'status':'unmatched', **missing[target]})
            continue
        depth = depths[target]
        if target in original and original[target]['depth_sha256'] != depth['depth_sha256']:
            raise ValueError('Previously associated sensor frame changed')
        path = prepared/depth['depth']
        if digest(path) != depth['depth_sha256']:
            raise ValueError('Target sensor bytes changed')
        with Image.open(path) as im:
            truth = np.array(im)
        a = anchors[target]
        raw_camera = dict(model=a['camera_model'], width=a['width'], height=a['height'], params=a['camera_params'])
        if raw_camera not in result['sfm']['original_cameras'] or truth.shape != (a['height'], a['width']):
            raise ValueError('Raw sensor and original RGB calibrations no longer match')
        both = load_map(args.predictions, saved, pair)
        _, backward = frozen_reverse(args.reverse, pair, rev)
        scores = score_pair(pair, both, backward, anchors[target], truth, config, scale)
        if record['previously_scored']:
            for new_key, old_key in [('shared','gmdepth'),('independent','separate_reverse')]:
                assert_scores_close(scores[new_key], prior['target'][old_key])
        rows.append({**record, **scores, **{k:depth[k] for k in ('depth_sha256','rgb_timestamp','depth_timestamp','time_delta_seconds')},
                     'status':'scored'})
    out = dict(status='complete', pairs_sha256=digest(args.pairs), old_score_sha256=digest(args.old_score),
               archive_sha256=references['archive_sha256'], sensor_evaluation_sha256=old['evaluation_sha256'],
               scale_m_per_sfm_unit=scale, total_seconds=time.monotonic()-started, rows=rows,
               all_targets=aggregate(rows), new_targets=aggregate([r for r in rows if not r['previously_scored']]))
    save(args.output/'scores.json', out)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pairs','predictions','reverse','old-score','sensor-root','archive','output'):
        parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
