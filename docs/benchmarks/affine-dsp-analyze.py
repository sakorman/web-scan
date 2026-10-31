"""Validate and score the affine/DSP experiment, including raw-image support."""

import argparse
import json
import sqlite3
import statistics
from pathlib import Path

import numpy as np
import pycolmap

from scripts.compare_raw_quality import compare
from webscan.brush import read_ply
from webscan.raw_inputs import resolve_raw_input


def local_support(work, model):
    """Describe spatial support without assigning a quality threshold."""
    valid = {
        pid for pid, p in model.points3D.items()
        if p.track.length() >= 2 and np.isfinite(p.error) and p.error < 4
    }
    with sqlite3.connect(f"file:{work / 'sfm/database.db'}?mode=ro", uri=True) as db:
        names = dict(db.execute('SELECT image_id,name FROM images'))
        edges = list(db.execute('SELECT pair_id,rows FROM two_view_geometries WHERE rows>0'))
    incident = {name: [] for name in names.values()}
    for pair_id, count in edges:
        first, second = divmod(pair_id, 2147483647)
        incident[names[first]].append((names[second], count))
        incident[names[second]].append((names[first], count))
    rows = []
    for im in sorted(model.images.values(), key=lambda im: im.name):
        cam = model.cameras[im.camera_id]
        grid = np.zeros((4, 4), dtype=int)
        tracks, invalid = [], 0
        for obs in im.points2D:
            if not obs.has_point3D() or obs.point3D_id not in valid:
                continue
            xy = np.asarray(obs.xy, dtype=float)
            if not np.all(np.isfinite(xy)) or np.any(xy < 0) or np.any(xy >= [cam.width, cam.height]):
                invalid += 1
                continue
            x, y = np.floor(xy / [cam.width, cam.height] * 4).astype(int)
            grid[y, x] += 1
            tracks.append(model.points3D[obs.point3D_id].track.length())
        count = int(grid.sum())
        assert count == len(tracks)
        rows.append({
            'image_name': im.name,
            'valid_observations': count,
            'excluded_invalid_xy': invalid,
            'grid_counts': grid.tolist(),
            'occupied_cells': int(np.count_nonzero(grid)),
            'largest_cell_fraction': float(grid.max() / count) if count else None,
            'median_track_length': float(np.median(tracks)) if tracks else None,
            'verified_neighbors': len(incident[im.name]),
            'incident_inliers': sum(n for _, n in incident[im.name]),
            'pairs': [{'other_image': name, 'inliers': n} for name, n in sorted(incident[im.name])],
        })
    assert sum(row['incident_inliers'] for row in rows) == 2 * sum(n for _, n in edges)
    return {
        'domain': 'original COLMAP chosen model; 4x4 uniform raw-image grid',
        'point_filter': 'track length >= 2, finite reprojection error < 4 px',
        'valid_points': len(valid),
        'note': 'Descriptive only: occupied cells and counts do not prove surface coverage or pose accuracy.',
        'views': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    runs, comparisons = [], []
    for scene in ('pipes', 'office'):
        for repeat in (1, 2):
            names = [f'{scene}-r{repeat}-{mode}' for mode in ('baseline', 'candidate')]
            for name in names:
                work = root / 'jobs' / name
                run = json.loads((work / 'result.json').read_text())
                assert run['status'] == 'complete'
                saved = json.loads((work / 'sfm/scene.json').read_text())
                model = pycolmap.Reconstruction(work / 'sfm/chosen')
                originals = {im.name: model.cameras[im.camera_id].todict() for im in model.images.values()}
                assert len(originals) == saved['registered_images'] == saved['input_images']
                assert not saved['unregistered_images']
                for v in saved['views']:
                    assert v['original_camera']['params'] == list(originals[v['image_name']]['params'])
                _, values = read_ply(work / 'artifacts/gaussians.ply', expected_sh_degree=3)
                assert len(values) == run['report']['gaussians']
                for photo in run['inputs']:
                    _, digest, _ = resolve_raw_input(repo / '.data', repo / '.data/jobs' / run['source_job'], photo['name'])
                    assert digest == photo['sha256']
                run['camera_and_sh3_verified'] = True
                run['local_support'] = local_support(work, model)
                runs.append(run)
            result = compare(root, *names, size=512, images_dir=root / 'images' / f'{scene}-r{repeat}')
            (root / f'{scene}-r{repeat}-raw.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            comparisons.append({'scene': scene, 'repeat': repeat, **result})
    summary = []
    for scene in ('pipes', 'office'):
        grouped = [r for r in runs if r['name'].startswith(scene)]
        scored = [p for p in comparisons if p['scene'] == scene]
        row = {'scene': scene}
        for mode in ('baseline', 'candidate'):
            selected = [r for r in grouped if r['variant'] == mode]
            row[mode] = {k: statistics.mean(r[k] for r in selected) for k in ('sfm_seconds', 'training_export_seconds', 'end_to_end_seconds', 'model_bytes')}
            row[mode]['gaussians'] = statistics.mean(r['report']['gaussians'] for r in selected)
            row[mode].update({k: statistics.mean(p['mean_per_view'][mode][k] for p in scored) for k in ('psnr', 'ssim')})
        row['candidate_minus_baseline'] = {k: row['candidate'][k] - row['baseline'][k] for k in row['baseline']}
        summary.append(row)
    output = {'config': json.loads((root / 'config.json').read_text()), 'runs': runs, 'comparisons': comparisons, 'summary': summary}
    (root / 'summary.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
