"""Compare saved Brush eval images at a common size and identical photo views."""

import argparse
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from webscan.evaluation import validate_evaluation


def metrics(render, reference):
    a = np.asarray(render).astype(np.float64) / 255
    b = np.asarray(reference).astype(np.float64) / 255
    mse = np.mean((a - b) ** 2)
    psnr = float(-10 * np.log10(max(mse, 1e-12)))
    x = torch.tensor(a.transpose(2, 0, 1)[None], dtype=torch.float32)
    y = torch.tensor(b.transpose(2, 0, 1)[None], dtype=torch.float32)
    axis = torch.arange(11, dtype=torch.float32) - 5
    kernel = torch.exp(-(axis**2) / (2 * 1.5**2))
    kernel /= kernel.sum()
    window = (kernel[:, None] * kernel[None, :]).expand(3, 1, 11, 11)

    def blur(v):
        return F.conv2d(v, window, groups=3)

    mx, my = blur(x), blur(y)
    vx, vy, cov = blur(x * x) - mx * mx, blur(y * y) - my * my, blur(x * y) - mx * my
    ssim = (
        (2 * mx * my + 0.01**2)
        * (2 * cov + 0.03**2)
        / ((mx * mx + my * my + 0.01**2) * (vx + vy + 0.03**2))
    ).mean()
    return {"psnr": psnr, "ssim": float(ssim)}


def _lpips_scores(scorer, directory, count):
    """Run the optional offline Brush scorer and require exactly one result per pair."""
    scorer = Path(scorer).resolve()
    with scorer.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    identity = json.loads(
        subprocess.run(
            [str(scorer), "--version"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    )
    if (
        not isinstance(identity, dict)
        or identity.get("protocol") != 1
        or identity.get("metric") != "lpips_vgg_brush"
    ):
        raise ValueError("Unsupported LPIPS scorer protocol or metric")
    weight_hash = identity.get("weights_sha256")
    if (
        not isinstance(weight_hash, str)
        or len(weight_hash) != 64
        or any(c not in "0123456789abcdef" for c in weight_hash.lower())
    ):
        raise ValueError("LPIPS scorer must identify its compiled weights")
    started = time.monotonic()
    output = subprocess.run(
        [str(scorer), str(directory)], check=True, capture_output=True, text=True, timeout=600
    ).stdout
    values = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise ValueError("Invalid LPIPS score row")
        index, score = int(fields[0]), float(fields[1])
        if index in values or not 0 <= index < count or not math.isfinite(score) or score < 0:
            raise ValueError("Duplicate, invalid or non-finite LPIPS score")
        values[index] = score
    if set(values) != set(range(count)):
        raise ValueError("Incomplete LPIPS scores")
    return [values[i] for i in range(count)], {
        **identity,
        "binary_sha256": digest,
        "seconds": time.monotonic() - started,
        "lower_is_better": True,
    }


def compare(data, quick, detail, size=512, lpips_scorer=None):
    if size < 16:
        raise ValueError("Comparison size must be at least 16 pixels")
    qa = data / "jobs" / quick
    de = data / "jobs" / detail
    qcam = json.loads((qa / "artifacts/cameras.json").read_text())
    dcam = json.loads((de / "artifacts/cameras.json").read_text())
    if qcam != dcam:
        raise ValueError("Camera geometry must be identical")
    qr = json.loads((qa / "artifacts/report.json").read_text())
    dr = json.loads((de / "artifacts/report.json").read_text())
    records = []
    for work, report in ((qa, qr), (de, dr)):
        if report.get("evaluation_mode", "holdout") != "holdout":
            raise ValueError("Comparison requires held-out evaluation")
        scene = json.loads((work / "sfm/scene.json").read_text())
        # Earlier reports did not record resolution; recover it from the actual PNG.
        resolution = report.get("resolution")
        if resolution is None:
            first = next(
                (work / "brush" / f"eval_{report['steps']}").glob("*.png"), None
            )
            if first is None:
                raise ValueError("No evaluation renders")
            with Image.open(first) as image:
                resolution = max(image.size)
        checked, _ = validate_evaluation(
            scene,
            work / "brush",
            {
                "steps": report["steps"],
                "resolution": resolution,
                "evaluation_mode": "holdout",
            },
            (work / "brush.log").read_text(errors="replace"),
        )
        if (
            "evaluation_images" in report
            and report["evaluation_images"] != checked["evaluation_images"]
        ):
            raise ValueError("Evaluation images or references changed since training")
        records.append(checked["evaluation_images"])
    if [(x["name"], x["reference_sha256"]) for x in records[0]] != [
        (x["name"], x["reference_sha256"]) for x in records[1]
    ]:
        raise ValueError("Evaluation views or reference images differ")
    qdir = qa / "brush" / f"eval_{qr['steps']}"
    ddir = de / "brush" / f"eval_{dr['steps']}"
    names = [record["name"] for record in records[0]]
    rows, pairs = [], []
    context = TemporaryDirectory(prefix="webscan-lpips-") if lpips_scorer else nullcontext(None)
    with context as folder:
        for name in names:
            with Image.open(
                qa / "sfm/undistorted/images" / (Path(name).stem + ".jpg")
            ) as source:
                reference = source.convert("RGB")
                reference.thumbnail((size, size), Image.Resampling.LANCZOS)
            with Image.open(qdir / name) as image:
                qimage = image.convert("RGB").resize(reference.size, Image.Resampling.LANCZOS)
            with Image.open(ddir / name) as image:
                dimage = image.convert("RGB").resize(reference.size, Image.Resampling.LANCZOS)
            row = {
                "view": name,
                "quick": metrics(qimage, reference),
                "detail": metrics(dimage, reference),
            }
            if folder is not None:
                if min(reference.size) < 16:
                    raise ValueError("LPIPS needs image dimensions of at least 16 pixels")
                provenance = {}
                for mode, image in [("quick", qimage), ("detail", dimage)]:
                    index = len(pairs)
                    render_path = Path(folder) / f"{index}-render.png"
                    reference_path = Path(folder) / f"{index}-reference.png"
                    image.save(render_path)
                    reference.save(reference_path)
                    pairs.append(f"{index}\t{render_path.name}\t{reference_path.name}")
                    provenance[mode] = {
                        "render_sha256": hashlib.sha256(render_path.read_bytes()).hexdigest(),
                        "reference_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                    }
                row["lpips_inputs"] = {
                    "width": reference.width, "height": reference.height, **provenance
                }
            rows.append(row)
        if folder is not None:
            (Path(folder) / "pairs.tsv").write_text("\n".join(pairs) + "\n")
            scores, scorer_info = _lpips_scores(lpips_scorer, folder, len(pairs))
            for index, row in enumerate(rows):
                row["quick"]["lpips"] = scores[index * 2]
                row["detail"]["lpips"] = scores[index * 2 + 1]
    metric_names = ["psnr", "ssim", "lpips"] if lpips_scorer else ["psnr", "ssim"]
    result = {
        "comparison_size": size,
        "quick_job": quick,
        "detail_job": detail,
        "views": rows,
        "mean": {
            mode: {
                key: float(np.mean([row[mode][key] for row in rows]))
                for key in metric_names
            }
            for mode in ["quick", "detail"]
        },
    }
    if lpips_scorer:
        result["lpips_scorer"] = scorer_info
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path(".data"))
    p.add_argument("--quick", required=True)
    p.add_argument("--detail", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--size", type=int, default=512, help="Common scoring long edge")
    p.add_argument(
        "--lpips-scorer", type=Path, help="Optional offline Brush LPIPS scorer executable"
    )
    args = p.parse_args()
    torch.set_num_threads(4)
    result = compare(args.data, args.quick, args.detail, args.size, args.lpips_scorer)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
