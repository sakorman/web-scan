"""Score a completed rotation-rate trial against regions fixed from prior reconstructions."""
import argparse
import json
from pathlib import Path
from statistics import mean

import torch

from scripts.benchmark_exports import sha
from scripts.compare_quality import compare


def score(run, regions):
    if set(regions) != {"pipes", "office"}:
        raise ValueError("Expected region selections for both pipes and office")
    records = json.loads((run / "results.json").read_text())
    if len(records) != 8 or not json.loads((run / "complete.json").read_text())["identities_match"]:
        raise ValueError("Expected eight completed and matched reconstructions")
    results = {record["name"]: record for record in records}
    pairs = []
    for scene, history in regions.items():
        work = run / "jobs" / f"{scene}-baseline-1"
        manifest = json.loads((work / "inputs.json").read_text())
        cameras = json.loads((work / "artifacts/cameras.json").read_text())
        for job in history["jobs"]:
            source = Path(".data/jobs") / job
            if json.loads((source / "artifacts/cameras.json").read_text()) != cameras:
                raise ValueError("Historical cameras differ from the trial")
            folder = source / "sfm/undistorted"
            files = [{"name": str(p.relative_to(folder)), "sha256": sha(p)}
                     for p in sorted(folder.rglob("*")) if p.is_file()]
            if files != manifest:
                raise ValueError("Historical reconstruction inputs differ from the trial")
        for repeat in (1, 2):
            baseline = f"{scene}-baseline-{repeat}"
            candidate = f"{scene}-candidate-{repeat}"
            measured = compare(run, baseline, candidate, size=512, error_grid=8)
            selected = history["tiles"]
            if set(selected) != {row["view"] for row in measured["views"]}:
                raise ValueError("Scored holdouts differ from the frozen region set")
            if not any(selected.values()):
                raise ValueError(f"No region selected for {scene}")
            for indices in selected.values():
                if len(set(indices)) != len(indices) or any(type(i) is not int or not 0 <= i < 64 for i in indices):
                    raise ValueError("Expected distinct row-major indices from 0 to 63")
            pair = {"scene": scene, "repeat": repeat, "models": {}}
            for name, label in ((baseline, "quick"), (candidate, "detail")):
                record = results[name]
                total_error, total_pixels = 0.0, 0
                views = []
                for view in measured["views"]:
                    indices = selected[view["view"]]
                    tiles = view[label]["error_tiles"]
                    weighted_error, pixels = 0.0, 0
                    for i in indices:
                        x0, y0, x1, y1 = tiles[i]["xyxy"]
                        size = (x1 - x0) * (y1 - y0)
                        weighted_error += size * tiles[i]["mse"]
                        pixels += size
                    views.append({"view": view["view"], "psnr": view[label]["psnr"],
                                  "ssim": view[label]["ssim"], "region_pixels": pixels,
                                  "region_mse": weighted_error / pixels if pixels else None})
                    total_error += weighted_error
                    total_pixels += pixels
                fields = ("name", "inputs_sha256", "cameras_sha256", "source_job", "original_camera",
                          "model_bytes", "model_sha256", "input_images", "registered_images",
                          "unregistered_images", "sfm_seconds", "training_export_seconds",
                          "reuse_pipeline_seconds")
                model = {key: record[key] for key in fields}
                model.update({key: record["report"][key] for key in
                              ("gaussians", "training_views", "evaluation_views", "training_seed",
                               "engine_sha256", "evaluation_verified", "color_model")})
                model.update(measured["mean"][label], views=views, region_mse=total_error / total_pixels)
                pair["models"][record["variant"]] = model
            models = pair["models"]
            pair["deltas"] = {key: models["candidate"][key] - models["baseline"][key]
                              for key in ("psnr", "ssim", "region_mse")}
            pairs.append(pair)
    thresholds = json.loads((run / "plan.json").read_text())["threshold"]
    deltas = [pair["deltas"] for pair in pairs]
    accepted = all(d["psnr"] >= thresholds["min_psnr_delta"] and
                   d["ssim"] >= thresholds["min_ssim_delta"] and d["region_mse"] < 0
                   for d in deltas)
    for scene in regions:
        accepted &= mean(p["deltas"]["psnr"] for p in pairs if p["scene"] == scene) >= thresholds["min_scene_mean_psnr_gain"]
    return {"plan": json.loads((run / "plan.json").read_text()), "regions": regions,
            "pairs": pairs, "passed": accepted}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(4)
    result = score(args.run, json.loads(args.regions.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
