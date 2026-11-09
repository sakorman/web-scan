"""Test whether a shared inverse-depth interval reproduces separately fitted reverse maps."""
import argparse
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from PIL import Image

from scripts.benchmark_depth_prior import digest, save
from scripts.benchmark_gmdepth import UPSTREAM, WEIGHTS_SHA256
from scripts.benchmark_gmdepth_cycle import load_prediction, read, reverse_pair, validate_pairs


def tensor_pair(row, device, torch):
    values = []
    mean = torch.tensor([.485, .456, .406], device=device)[None, :, None, None]
    std = torch.tensor([.229, .224, .225], device=device)[None, :, None, None]
    for view in (row['source'], row['target']):
        with Image.open(view['image']) as image:
            if list(image.size) != view['size']:
                raise ValueError('Photo size changed')
            pixels = np.array(image.convert('RGB').resize(row['size'], Image.Resampling.BILINEAR))
        values.append((torch.from_numpy(pixels).permute(2, 0, 1)[None].float().to(device)/255-mean)/std)
    return values


def compare_arrays(actual, reference):
    if (actual.shape != reference.shape or actual.size == 0 or not np.isfinite(actual).all()
            or not np.isfinite(reference).all() or np.any(actual <= 0) or np.any(reference <= 0)):
        raise ValueError('Expected positive finite depths on the same grid')
    diff = np.abs(actual.astype(np.float64) / reference - 1)
    return dict(identical=bool(np.array_equal(actual, reference)),
                mean_relative=float(diff.mean()), max_relative=float(diff.max()))


def frozen_reverse(directory, row, manifest_row):
    path = directory/manifest_row['directory']/'pairs.json'
    if (digest(path) != manifest_row['pairs_sha256'] or read(path)['records'] != [reverse_pair(row)]
            or (manifest_row['source'], manifest_row['target']) != (row['source']['name'], row['target']['name'])):
        raise ValueError('Reverse pair identity changed')
    rev = read(path)['records'][0]
    depth, _ = load_prediction(path.parent/'inference', path, rev['source']['name'], rev['size'])
    return rev, depth


def run(args):
    import torch
    pairs = validate_pairs(args.pairs)
    index = read(args.reverse/'index.json')
    if index['forward_pairs_sha256'] != digest(args.pairs):
        raise ValueError('Reverse index belongs to another reconstruction')
    if (subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', 'HEAD'], text=True).strip() != UPSTREAM
            or subprocess.check_output(['git', '-C', str(args.repo), 'status', '--porcelain'], text=True).strip()
            or digest(args.weights) != WEIGHTS_SHA256):
        raise ValueError('UniMatch checkout or weights changed')
    sys.path.insert(0, str(args.repo.resolve()))
    from unimatch.unimatch import UniMatch
    torch.set_num_threads(4)
    model = UniMatch(task='depth').eval()
    model.load_state_dict(torch.load(args.weights, map_location='cpu', weights_only=True)['model'], strict=True)
    model.to(args.device)
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for row, entry in zip(pairs['records'], index['records'], strict=True):
        forward, _ = load_prediction(args.forward, args.pairs, row['source']['name'], row['size'])
        rev, backward = frozen_reverse(args.reverse, row, entry)
        images = tensor_pair(row, args.device, torch)

        def predict(frames, geometry, bidirectional):
            k = torch.tensor(geometry['K'], dtype=torch.float32, device=args.device)[None]
            pose = torch.tensor(geometry['pose'], dtype=torch.float32, device=args.device)[None]
            if args.device == 'mps':
                torch.mps.synchronize()
            begin = time.monotonic()
            with torch.inference_mode():
                z = model(*frames, task='depth', intrinsics=k, pose=pose, min_depth=1/row['far'],
                          max_depth=1/row['near'], attn_type='swin', attn_splits_list=[2],
                          prop_radius_list=[-1], num_depth_candidates=64,
                          pred_bidir_depth=bidirectional)['flow_preds'][-1]
            if args.device == 'mps':
                torch.mps.synchronize()
            elapsed = time.monotonic()-begin
            return z.cpu().numpy(), elapsed

        both, seconds = predict(images, row, True)
        # Same separate reverse geometry and weights as the frozen run; vary only its search interval.
        matched, matched_seconds = predict(images[::-1], rev, False)
        if both.shape != (2, *forward.shape) or matched.shape != (1, *backward.shape):
            raise ValueError('Unexpected depth-map dimensions')
        path = args.output/(Path(row['source']['name']).stem+'.npy')
        np.save(path, both)
        rows.append(dict(source=row['source']['name'], target=row['target']['name'], map=path.name,
                         map_sha256=digest(path), source_range=[row['near'], row['far']],
                         target_range=[rev['near'], rev['far']], forward=compare_arrays(both[0], forward),
                         reverse_target_range=compare_arrays(both[1], backward),
                         reverse_matched_range=compare_arrays(both[1], matched[0]),
                         seconds=seconds, matched_reverse_seconds=matched_seconds))
    save(args.output/'bidir.json', dict(status='complete', pairs_sha256=digest(args.pairs),
         reverse_index_sha256=digest(args.reverse/'index.json'), upstream=UPSTREAM, weights_sha256=WEIGHTS_SHA256,
         device=args.device, views=rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pairs', 'forward', 'reverse', 'repo', 'weights', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--device', choices=['cpu', 'mps'], required=True)
    run(parser.parse_args())
