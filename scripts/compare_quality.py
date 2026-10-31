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


class ReferenceMetrics:
    """Reuse only this view's fixed reference statistics for its paired renders."""

    def __init__(self, reference):
        self.reference = np.asarray(reference).astype(np.float64) / 255
        self.y = torch.tensor(self.reference.transpose(2, 0, 1)[None], dtype=torch.float32)
        axis = torch.arange(11, dtype=torch.float32) - 5
        kernel = torch.exp(-(axis**2) / (2 * 1.5**2))
        kernel /= kernel.sum()
        self.window = (kernel[:, None] * kernel[None, :]).expand(3, 1, 11, 11)
        self.my = self.blur(self.y)
        self.vy = self.blur(self.y * self.y) - self.my * self.my

    def blur(self, value):
        return F.conv2d(value, self.window, groups=3)

    def score(self, render):
        a = np.asarray(render).astype(np.float64) / 255
        mse = np.mean((a - self.reference) ** 2)
        psnr = float(-10 * np.log10(max(mse, 1e-12)))
        x = torch.tensor(a.transpose(2, 0, 1)[None], dtype=torch.float32)
        mx, my, vy = self.blur(x), self.my, self.vy
        vx = self.blur(x * x) - mx * mx
        cov = self.blur(x * self.y) - mx * my
        ssim = (
            (2 * mx * my + 0.01**2)
            * (2 * cov + 0.03**2)
            / ((mx * mx + my * my + 0.01**2) * (vx + vy + 0.03**2))
        ).mean()
        return {"psnr": psnr, "ssim": float(ssim)}


def metrics(render, reference):
    return ReferenceMetrics(reference).score(render)


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
        # Earlier reports did not record the cap. The first photo can be smaller
        # than that cap, so inspect every rendering rather than assuming its size.
        resolution = report.get("resolution")
        if resolution is None:
            images = list((work / "brush" / f"eval_{report['steps']}").glob("*.png"))
            if not images:
                raise ValueError("No evaluation renders")
            resolution = 0
            for path in images:
                with Image.open(path) as image:
                    resolution = max(resolution, *image.size)
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
    common_size = min(
        size,
        *(max(record["width"], record["height"]) for group in records for record in group),
    )
    if common_size < 16:
        raise ValueError("Native evaluation renders must be at least 16 pixels on the long edge")
    qdir = qa / "brush" / f"eval_{qr['steps']}"
    ddir = de / "brush" / f"eval_{dr['steps']}"
    names = [record["name"] for record in records[0]]
    rows, pairs = [], []
    context = TemporaryDirectory(prefix="webscan-lpips-") if lpips_scorer else nullcontext(None)
    with context as folder:
        for view_index, name in enumerate(names):
            quick_native = [records[0][view_index][axis] for axis in ["width", "height"]]
            detail_native = [records[1][view_index][axis] for axis in ["width", "height"]]
            with Image.open(
                qa / "sfm/undistorted/images" / (Path(name).stem + ".jpg")
            ) as source:
                reference = source.convert("RGB")
                reference_native = list(reference.size)
                reference.thumbnail((common_size, common_size), Image.Resampling.LANCZOS)
                # A renderer may round the short edge down by one pixel. Preserve
                # the usual grid when it fits; otherwise shrink within both outputs.
                bounds = tuple(min(a, b) for a, b in zip(quick_native, detail_native))
                if any(actual > limit for actual, limit in zip(reference.size, bounds)):
                    reference = source.convert("RGB")
                    reference.thumbnail(
                        tuple(min(limit, common_size) for limit in bounds),
                        Image.Resampling.LANCZOS,
                    )
            if min(reference.size) < 11:
                raise ValueError("SSIM needs image dimensions of at least 11 pixels")
            if lpips_scorer and min(reference.size) < 16:
                raise ValueError("LPIPS needs image dimensions of at least 16 pixels")
            with Image.open(qdir / name) as image:
                qimage = image.convert("RGB").resize(reference.size, Image.Resampling.LANCZOS)
            with Image.open(ddir / name) as image:
                dimage = image.convert("RGB").resize(reference.size, Image.Resampling.LANCZOS)
            scorer = ReferenceMetrics(reference)
            row = {
                "view": name,
                "quick": scorer.score(qimage),
                "detail": scorer.score(dimage),
                "scoring_dimensions": {
                    "width": reference.width,
                    "height": reference.height,
                    "reference_native": reference_native,
                    "quick_native": quick_native,
                    "detail_native": detail_native,
                },
            }
            # Do not keep full-resolution reference tensors across views.
            del scorer
            if folder is not None:
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
        "requested_comparison_size": size,
        "comparison_size": common_size,
        "resize_policy": "never_upscale",
        "resampling": "Lanczos",
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
    p.add_argument(
        "--size", type=int, default=512,
        help="Requested scoring long-edge cap (also limited by both native renders)",
    )
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
