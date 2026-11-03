"""Assemble the public count tables and baseline figure from the local real-photo runs."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from scripts.benchmark_depth_prior import digest, save
from webscan.brush import read_ply

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
root = parser.parse_args().root
old = Path(".data/benchmarks/depth-consistency-20261101")
read = lambda path: json.loads(path.read_text())
measurements = {p.stem: read(p) for p in sorted((root/"verified").glob("*.json"))}
assert set(measurements) == {"office-baseline", "pipes-baseline", "tum-frozen", "tum-new"}
repeat_equal = measurements["tum-new"]["views"] == measurements["tum-frozen"]["views"]
baselines = {}
for label, parent in [("office", old), ("pipes", old), ("tum", root)]:
    work = parent / "jobs" / (label+"-baseline")
    record = read(work/"result.json")
    report = record["report"]
    assert record["status"] == "complete" and report["evaluation_verified"]
    model = work / "artifacts/gaussians.ply"
    _, vertices = read_ply(model, expected_sh_degree=3)
    assert model.stat().st_size == record["model_bytes"] and len(vertices) == report["gaussians"]
    assert all(digest(work/"input"/v["name"]) == v["sha256"] for v in record["inputs"])
    baselines[label] = {k: record[k] for k in ["sfm", "sfm_seconds", "training_export_seconds",
                                                "end_to_end_seconds", "model_bytes"]}
    baselines[label].update({k: report[k] for k in ["engine_version","training_seed","steps","resolution",
                                                      "training_views","evaluation_views","gaussians"]})
    baselines[label]["model_sha256"] = digest(model)
    baselines[label]["score"] = (read(root/"rgb-scores.json")["mean"]["quick"] if label == "tum"
                                 else read(old/"baseline-scores.json")[label]["mean"])
    baselines[label]["held_out_views"] = [v["name"] for v in report["evaluation_images"]]
save("docs/benchmarks/depth-overlap.json", {"radii": [0.005, 0.01, 0.02], "primary_radius": 0.01,
     "unit": "image-grid observations, not fused 3D points", "diagnostics": measurements,
     "repeat_view_counts_identical": repeat_equal, "rgb_baselines": baselines, "new_rgb_scores": read(root/"rgb-scores.json")["views"]})

rows = measurements["tum-new"]["views"]
groups = [r["levels"]["0.01"] for r in rows]
strict = np.array([r["clean_within_spatial_radius"] for r in groups])
loose = np.array([r["clean_sensor_far_and_within_10pct"] for r in groups])
avail = np.array([r["clean_sensor_valid"] for r in groups])
samples = np.array([r["both_far_no_front_conflict"] for r in groups])
fig = plt.figure(figsize=(10, 7.8), layout="constrained")
g = fig.add_gridspec(2, 2, height_ratios=[1, 1.1])
ax = fig.add_subplot(g[0, :])
x = np.arange(len(rows))
bottom = np.zeros(len(rows))
for values, label, color in [(strict, "Within 1% spatial radius", "#167267"),
        (loose-strict, "Passes 10% depth check only", "#77c3b2"),
        (avail-loose, "Fails far / 10% depth check", "#d99445"), (samples-avail, "No sensor depth", "#bec7cf")]:
    ax.bar(x, values, bottom=bottom, color=color, label=label)
    bottom += values
ax.set_xticks(x, [r["name"][:4] for r in rows])
ax.set_ylabel("Check-grid samples")
ax.set_title("TUM · supported, far in image and space, no front conflict")
ax.legend(ncol=2, fontsize=9, loc="upper left")
work = root / "jobs/tum-baseline"
view = sorted((work/"brush/eval_3000").glob("*.png"))[0]
for i, (path, title) in enumerate([(work/"sfm/undistorted/images"/(view.stem+".jpg"), "Held-out RGB"),
                                   (view, "RGB-only baseline")]):
    with Image.open(path) as source:
        photo = source.convert("RGB")
    photo.thumbnail((512, 512), Image.Resampling.LANCZOS)
    assert photo.size == (512, 384)
    ax = fig.add_subplot(g[1, i]); ax.imshow(photo); ax.axis("off")
    ax.set_title(f"{title} · frame {view.stem[:4]}")
fig.savefig("docs/screenshots/195-depth-overlap.png", dpi=130)
plt.close(fig)
