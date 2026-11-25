"""Compare held-out RGB error in tiles with and without verified training tracks."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from scripts.benchmark_exports import sha
from scripts.benchmark_texture_support import texture_support
from scripts.compare_quality import compare


def group_errors(support, tiles):
    width, height = support["size"]
    counts = np.asarray(support["tile_tracks"])
    energy = np.asarray(support["tile_gradient_energy"])
    mse = np.empty((8, 8))
    area = np.empty((8, 8), dtype=int)
    if len(tiles) != 64 or counts.shape != mse.shape or energy.shape != mse.shape:
        raise ValueError("Expected an 8 by 8 grid")
    for index, tile in enumerate(tiles):
        row, col = divmod(index, 8)
        x0, x1 = col * width // 8, (col + 1) * width // 8
        y0, y1 = row * height // 8, (row + 1) * height // 8
        if tile["xyxy"] != [x0, y0, x1, y1]:
            raise ValueError("Scoring bounds differ from track grid")
        area[row, col] = (x1 - x0) * (y1 - y0)
        mse[row, col] = tile["mse"]

    lower, upper = support["energy_quartiles"]
    strata = {"all": np.ones_like(counts, dtype=bool)}
    if lower < upper:
        strata.update(low=energy <= lower, high=energy >= upper)
    result = {}
    for name, mask in strata.items():
        groups = {}
        for label, present in (("empty", counts == 0), ("supported", counts > 0)):
            selected = mask & present
            pixels = int(area[selected].sum())
            groups[label] = {
                "tiles": int(selected.sum()), "pixels": pixels,
                "mse": float(np.average(mse[selected], weights=area[selected])) if pixels else None,
            }
        result[name] = groups
    return result


def source_identity(work):
    folder = work / "sfm/undistorted"
    scene = json.loads((work / "sfm/scene.json").read_text())
    sparse = {p.name: sha(p) for p in (folder / "sparse").glob("*.bin")}
    photos = {v["image_name"]: sha(folder / "images" / v["image_name"]) for v in scene["views"]}
    return sparse, photos


def compare_support(data, first, second, audit_paths):
    work = data / "jobs" / first
    identity = source_identity(work)
    if not all(identity) or identity != source_identity(data / "jobs" / second):
        raise ValueError("Track comparison needs identical sparse models and source photos")
    scores = compare(data, first, second, size=512, error_grid=8)
    by_name = {row["view"]: row for row in scores["views"]}
    seen = set()
    for path in audit_paths:
        audit = json.loads(path.read_text())
        if (audit["sparse_sha256"], audit["photo_sha256"]) != identity:
            raise ValueError("Track audit differs from the scored sparse model or photos")
        name = audit["focus_image"]
        render = Path(name).with_suffix(".png").name
        if render not in by_name or render in seen or name != Path(name).name:
            raise ValueError("Unexpected or repeated audited holdout")
        seen.add(render)
        row = by_name[render]
        dims = row["scoring_dimensions"]
        size = (dims["width"], dims["height"])
        support = texture_support(audit, work / "sfm/undistorted/images" / name, scoring_size=size)
        row["reference_sha256"] = support["reference_sha256"]
        for variant in ("quick", "detail"):
            metrics = row[variant]
            metrics["support_error"] = group_errors(support, metrics.pop("error_tiles"))
    if seen != set(by_name):
        raise ValueError("Each scored holdout needs its track audit")
    return scores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(".data"))
    parser.add_argument("--quick", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--audits", required=True, nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    torch.set_num_threads(4)
    result = compare_support(args.data, args.quick, args.detail, args.audits)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
