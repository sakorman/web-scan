"""Measure recurring high RGB errors on a shared native evaluation grid."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from scripts.compare_quality import compare, scoring_reference


def high_error_mask(error):
    """Select at most ceil(N/10) positive errors, dropping ties across the cut."""
    values = error.ravel()
    limit = (values.size + 9) // 10
    if values.size <= limit:
        return error > 0
    # The (limit + 1)-th largest error must be below every selected pixel.
    cutoff_index = values.size - limit - 1
    cutoff = np.partition(values, cutoff_index)[cutoff_index]
    return error > cutoff


def measure_tile(history, check):
    frozen = np.logical_and.reduce([high_error_mask(image) for image in history])
    top = high_error_mask(check)
    stats = {
        "pixels": check.size,
        "frozen_pixels": int(frozen.sum()),
        "frozen_sha256": hashlib.sha256(frozen.astype(np.uint8).tobytes()).hexdigest(),
        "check_top_pixels": int(top.sum()),
        "in_both": int((frozen & top).sum()),
        "frozen_rgb_sse": int(check[frozen].sum()),
        "all_rgb_sse": int(check.sum()),
    }
    return stats, frozen


def measure(data, history, check, regions, size=512, masks_dir=None):
    jobs = [*history, check]
    directories = [data / "jobs" / job for job in jobs]
    if len(history) < 2 or len({p.resolve() for p in directories}) != len(jobs):
        raise ValueError("Provide distinct jobs and at least two historical reconstructions")
    comparisons = [compare(data, history[0], job, size, error_grid=8) for job in jobs[1:]]
    views = comparisons[0]["views"]
    if set(regions) != {v["view"] for v in views}:
        raise ValueError("Region keys must match the verified holdouts")
    steps = [json.loads((job / "artifacts/report.json").read_text())["steps"] for job in directories]
    results = []
    masks = {}
    for row, view in enumerate(views):
        name = view["view"]
        indices = regions[name]
        if any(type(i) is not int or not 0 <= i < 64 for i in indices) or len(set(indices)) != len(indices):
            raise ValueError("Expected distinct tile indices in the range 0 to 63")
        dims = view["scoring_dimensions"]
        grid = [dims["width"], dims["height"]]
        for comparison in comparisons:
            other = comparison["views"][row]
            frame = other["scoring_dimensions"]
            grids = ([frame["width"], frame["height"]], frame["quick_native"], frame["detail_native"])
            if other["view"] != name or any(actual != grid for actual in grids):
                raise ValueError("Pixel recurrence requires the same native render and scoring grid")
        path = directories[0] / "sfm/undistorted/images" / Path(name).with_suffix(".jpg")
        reference, _ = scoring_reference(path, comparisons[0]["comparison_size"], grid)
        reference = np.asarray(reference, dtype=np.int32)
        errors = []
        for directory, step in zip(directories, steps):
            with Image.open(directory / "brush" / f"eval_{step}" / name) as image:
                residual = np.asarray(image.convert("RGB"), dtype=np.int32) - reference
            errors.append((residual ** 2).sum(axis=2, dtype=np.int32))
        tiles = []
        if masks_dir is not None:
            masks[name] = np.zeros(reference.shape[:2], dtype=np.uint8)
        for i in indices:
            bounds = view["quick"]["error_tiles"][i]["xyxy"]
            left, top, right, bottom = bounds
            crops = [image[top:bottom, left:right] for image in errors]
            stats, frozen = measure_tile(crops[:-1], crops[-1])
            tiles.append({"index": i, "xyxy": bounds, **stats})
            if masks_dir is not None:
                masks[name][top:bottom, left:right] = 255 * frozen
        measured = comparisons[-1]["views"][row]["detail"]
        results.append({"view": name, "grid": grid, "psnr": measured["psnr"], "ssim": measured["ssim"],
                        "tiles": tiles})
    if masks_dir is not None:
        masks_dir.mkdir(parents=True, exist_ok=True)
        for name, mask in masks.items():
            Image.fromarray(mask).save(masks_dir / name)
    return {"history": history, "check": check, "mean": comparisons[-1]["mean"]["detail"], "views": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--history", nargs="+", required=True)
    parser.add_argument("--check", required=True)
    parser.add_argument("--regions", type=Path, required=True, help="JSON mapping holdout PNG names to 8x8 tile indices")
    parser.add_argument("--masks-dir", type=Path, help="Save one full-frame historical selection mask per holdout")
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(4)
    result = measure(args.data, args.history, args.check, json.loads(args.regions.read_text()), args.size, args.masks_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
