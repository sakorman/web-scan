"""Localize audited cross-view tracks by texture in a held-out reference photo."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def texture_support(audit, reference, *, scoring_size=None):
    name = audit["focus_image"]
    if name in audit["training_images"] or audit["holdout_color_samples"] != 0:
        raise ValueError("Expected a held-out focus image and training-only track colors")
    with reference.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != audit["photo_sha256"][name]:
            raise ValueError("Focus reference SHA-256 mismatch")
        stream.seek(0)
        with Image.open(stream) as source:
            native = source.size
            if list(native) != audit["focus_dimensions"]:
                raise ValueError("Focus reference dimensions differ from audit")
            image = source.convert("L")
    if scoring_size is None:
        image.thumbnail((512, 512), Image.Resampling.BILINEAR)
    else:
        # Use the exact grid chosen by the paired scorer, including rounded short edges.
        width, height = scoring_size
        if (not 0 < width <= native[0] or not 0 < height <= native[1]
                or abs(width * native[1] - height * native[0]) > max(native)):
            raise ValueError("Scoring dimensions do not preserve the reference frame")
        image = image.resize(scoring_size, Image.Resampling.BILINEAR, reducing_gap=2.0)
    width, height = image.size
    if min(width, height) < 24:
        raise ValueError("Each of the 8 x 8 tiles needs at least three pixels per axis")
    gray = np.asarray(image, dtype=np.float32) / 255
    x_edges = np.arange(9) * width // 8
    y_edges = np.arange(9) * height // 8
    energies = np.empty((8, 8), dtype=float)
    for row in range(8):
        for col in range(8):
            tile = gray[y_edges[row]:y_edges[row+1], x_edges[col]:x_edges[col+1]]
            dx = tile[1:-1, 2:] - tile[1:-1, :-2]
            dy = tile[2:, 1:-1] - tile[:-2, 1:-1]
            energies[row, col] = np.mean(dx*dx + dy*dy)

    counts = np.zeros((8, 8), dtype=int)
    points = set()
    for point in audit["focus_points"]:
        x, y = point["xy"]
        if not (0.5 <= x < native[0] - 0.5 and 0.5 <= y < native[1] - 0.5):
            raise ValueError("Audited point outside sampling bounds")
        if point["point_id"] in points or point["training_observations"] < 2:
            raise ValueError("Expected a unique point with two training observations")
        points.add(point["point_id"])
        # COLMAP coordinates are measured from the outer edge of the first pixel.
        col = np.searchsorted(x_edges, x * width / native[0], side="right") - 1
        row = np.searchsorted(y_edges, y * height / native[1], side="right") - 1
        counts[row, col] += 1

    lower, upper = np.quantile(energies, [0.25, 0.75]).tolist()
    groups = {}
    if lower < upper:
        for label, mask in (("low", energies <= lower), ("high", energies >= upper)):
            selected = counts[mask]
            groups[label] = {"tiles": int(selected.size),
                             "occupied": int(np.count_nonzero(selected)),
                             "tracks": int(selected.sum())}
    return {"focus_image": name, "reference_sha256": digest, "size": [width, height],
            "grid": [8, 8], "energy_quartiles": [lower, upper], "groups": groups,
            "track_count": len(points), "tile_tracks": counts.tolist(),
            "tile_gradient_energy": energies.tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path, help="benchmark_track_color.py JSON")
    parser.add_argument("--reference", required=True, type=Path, help="Original undistorted focus image")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = texture_support(json.loads(args.audit.read_text()), args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["groups"]))


if __name__ == "__main__":
    main()
