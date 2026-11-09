"""Check bidirectional maps against the fixed TUM sensor grid outside PyTorch."""
import argparse
from pathlib import Path

import numpy as np

from scripts.benchmark_depth_consistency import grid
from scripts.benchmark_depth_prior import digest, sample_depth, save
from scripts.benchmark_gmdepth_bidir import frozen_reverse
from scripts.benchmark_gmdepth_cycle import assert_scores_close, read, validate_pairs
from scripts.benchmark_sensor_depth import score_depth


def score_view(depth, view, size, evaluation, anchor, record, root):
    import pycolmap
    path = root/'evaluation'/record['map']
    if digest(path) != record['map_sha256'] or not np.allclose(anchor['cam_from_world'], np.asarray(view['pose'])[:3]):
        raise ValueError('Sensor reference or camera changed')
    camera = pycolmap.Camera(model=anchor['camera_model'], width=anchor['width'], height=anchor['height'],
                             params=anchor['camera_params'])
    xy, shape = grid(camera.width, camera.height, evaluation['config']['grid_step'])
    rays = camera.cam_from_img(xy)
    uv = (np.c_[rays, np.ones(len(rays))] @ np.asarray(view['K']).T)[:, :2] * np.asarray(size)/view['size']
    samples, inside = sample_depth(depth, uv)
    with np.load(path, allow_pickle=False) as data:
        if any(data[k].shape != tuple(shape) for k in ('check', 'sensor_m', 'prior_m')):
            raise ValueError('Sensor grid changed')
        keep = data['check'].ravel() & inside
        truth = data['sensor_m'].ravel()[keep]
        return dict(name=view['name'], grid_samples=int(keep.sum()),
                    gmdepth=score_depth(samples[keep]*evaluation['scale_m_per_sfm_unit'], truth),
                    monocular=score_depth(data['prior_m'].ravel()[keep], truth))


def load_map(directory, meta, row):
    if (meta['source'], meta['target']) != (row['source']['name'], row['target']['name']):
        raise ValueError('Prediction pair identity changed')
    path = directory/meta['map']
    if digest(path) != meta['map_sha256']:
        raise ValueError('Prediction bytes changed')
    array = np.load(path, allow_pickle=False)
    if array.shape != (2, *row['size'][::-1]) or not np.isfinite(array).all() or np.any(array <= 0):
        raise ValueError('Invalid bidirectional depth')
    return array


def run(args):
    pairs = validate_pairs(args.pairs)
    meta = read(args.predictions/'bidir.json')
    root = args.sensor_root
    evaluation = read(root/'evaluation/sensor-evaluation.json')
    manifest = read(root/'sensor/sensor.json')
    base = read(args.baseline/'scores.json')
    if (not all(d['status'] == 'complete' for d in (evaluation, base, meta)) or
            digest(args.pairs) != meta['pairs_sha256'] or meta['pairs_sha256'] != base['pairs_sha256'] or
            digest(args.reverse/'index.json') != meta['reverse_index_sha256'] or
            evaluation['manifest_sha256'] != digest(root/'sensor/sensor.json') or
            manifest['anchor_sha256'] != digest(root/'sensor/anchors.json') or
            base['sensor_evaluation_sha256'] != digest(root/'evaluation/sensor-evaluation.json')):
        raise ValueError('Frozen evaluation inputs changed')
    refs = {Path(v['name']).stem: v for v in evaluation['views']}
    anchors = {Path(v['name']).stem: v for v in read(root/'sensor/anchors.json')['views']}
    baseline = {v['name']: v['sensor'] for v in base['views']}
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for row, m, reverse in zip(pairs['records'], meta['views'], read(args.reverse/'index.json')['records'], strict=True):
        array = load_map(args.predictions, m, row)
        def score(depth, key):
            view = row[key]
            stem = Path(view['name']).stem
            return score_view(depth, view, row['size'], evaluation, anchors[stem], refs[stem], root)
        source = score(array[0], 'source')
        for method in ('gmdepth', 'monocular'):
            assert_scores_close(source[method], baseline[source['name']][method])
        target = None
        if Path(row['target']['name']).stem in refs:
            _, backward = frozen_reverse(args.reverse, row, reverse)
            target = score(array[1], 'target')
            target['separate_reverse'] = score(backward, 'target')['gmdepth']
        rows.append(dict(source=source, target=target))
    save(args.output/'sensor.json', dict(status='complete', views=rows, prediction_sha256=digest(args.predictions/'bidir.json'),
                                         evaluation_sha256=digest(root/'evaluation/sensor-evaluation.json')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pairs', 'predictions', 'reverse', 'baseline', 'sensor-root', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
