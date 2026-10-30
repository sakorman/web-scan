"""Compare two completed Brush jobs in the same original-photo pixel domain."""

import argparse
import json
from pathlib import Path

import numpy as np
import pycolmap
from PIL import Image

from webscan.evaluation import validate_evaluation
from webscan.raw_evaluation import masked_metrics, scaled_size, warp_render_to_raw
from webscan.raw_inputs import resolve_raw_input


def _job(data, job_id):
    root = data / "jobs" / job_id
    report = json.loads((root / "artifacts/report.json").read_text())
    if report.get("evaluation_mode") != "holdout":
        raise ValueError("原始像素对照需要留出评估任务")
    scene = json.loads((root / "sfm/scene.json").read_text())
    checked, _ = validate_evaluation(
        scene,
        root / "brush",
        {
            "steps": report["steps"],
            "resolution": report["resolution"],
            "evaluation_mode": "holdout",
        },
        (root / "brush.log").read_text(errors="replace"),
    )
    if report.get("evaluation_images") != checked["evaluation_images"]:
        raise ValueError("保存的留出图已变化或不完整")
    if all("original_camera" in view for view in scene["views"]):
        cameras = {
            view["image_name"]: pycolmap.Camera(**view["original_camera"])
            for view in scene["views"]
        }
    else:
        if not (root / "sfm/chosen").is_dir():
            raise ValueError("旧缓存任务缺少原始相机；请重新重建以保存原始域评测数据")
        reconstruction = pycolmap.Reconstruction(root / "sfm/chosen")
        cameras = {
            image.name: reconstruction.cameras[image.camera_id]
            for image in reconstruction.images.values()
        }
    return {
        "id": job_id,
        "root": root,
        "report": report,
        "scene_views": {view["image_name"]: view for view in scene["views"]},
        "cameras": cameras,
        "evaluation": {row["name"]: row for row in checked["evaluation_images"]},
    }


def _models(job):
    names = set()
    for row in job["evaluation"].values():
        image_name = Path(row["name"]).with_suffix(".jpg").name
        camera = job["cameras"].get(image_name)
        if camera is None:
            raise ValueError(f"找不到评估视角 {image_name} 的原始相机")
        names.add(camera.model.name)
    return sorted(names)


def _image(path):
    with Image.open(path) as source:
        return np.asarray(source.convert("RGB"), dtype=np.float64) / 255


def _save(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8)).save(path)


def _warp(job, evaluation_name, raw_path, output_size):
    image_name = Path(evaluation_name).with_suffix(".jpg").name
    view = job["scene_views"].get(image_name)
    camera = job["cameras"].get(image_name)
    if view is None or camera is None:
        raise ValueError(f"任务 {job['id']} 缺少 {image_name} 的相机数据")
    render = _image(job["root"] / "brush" / f"eval_{job['report']['steps']}" / evaluation_name)
    with Image.open(raw_path) as source:
        raw_size = source.size
    if raw_size != (camera.width, camera.height):
        raise ValueError(f"原始照片与 COLMAP 相机尺寸不一致：{image_name}")
    warped, mask = warp_render_to_raw(
        render,
        raw_size,
        camera,
        view["K"],
        (view["width"], view["height"]),
        output_size,
    )
    return warped, mask


def compare(data, baseline_id, candidate_id, size=512, images_dir=None):
    if size < 16:
        raise ValueError("评分长边至少为 16 像素")
    baseline, candidate = _job(data, baseline_id), _job(data, candidate_id)
    baseline_names = sorted(baseline["evaluation"])
    if baseline_names != sorted(candidate["evaluation"]):
        raise ValueError("两个任务的留出视角集合不一致")
    rows = []
    for evaluation_name in baseline_names:
        base_render = baseline["evaluation"][evaluation_name]
        candidate_render = candidate["evaluation"][evaluation_name]
        raw_name = Path(evaluation_name).with_suffix(".jpg").name
        base_raw, base_sha, base_identity = resolve_raw_input(data, baseline["root"], raw_name)
        candidate_raw, candidate_sha, candidate_identity = resolve_raw_input(data, candidate["root"], raw_name)
        if base_sha != candidate_sha:
            raise ValueError(f"原始照片身份不一致：{raw_name}")
        with Image.open(base_raw) as source:
            raw_size = source.size
        native_cap = min(
            size,
            max(base_render["width"], base_render["height"]),
            max(candidate_render["width"], candidate_render["height"]),
        )
        output_size = scaled_size(*raw_size, native_cap)
        base_warp, base_mask = _warp(
            baseline, evaluation_name, base_raw, output_size
        )
        candidate_warp, candidate_mask = _warp(
            candidate, evaluation_name, candidate_raw, output_size
        )
        with Image.open(base_raw) as source:
            reference = np.asarray(
                source.convert("RGB").resize(output_size, Image.Resampling.LANCZOS),
                dtype=np.float64,
            ) / 255
        common_mask = base_mask & candidate_mask
        base_metrics = masked_metrics(base_warp, reference, common_mask)
        candidate_metrics = masked_metrics(candidate_warp, reference, common_mask)
        if images_dir:
            prefix = Path(images_dir) / Path(evaluation_name).stem
            _save(prefix.with_name(prefix.name + "-reference.png"), reference)
            _save(prefix.with_name(prefix.name + "-baseline.png"), base_warp)
            _save(prefix.with_name(prefix.name + "-candidate.png"), candidate_warp)
        rows.append(
            {
                "view": evaluation_name,
                "raw_input_sha256": base_sha,
                "raw_identity": {"baseline": base_identity, "candidate": candidate_identity},
                "original_size": {"width": raw_size[0], "height": raw_size[1]},
                "scoring_size": {"width": output_size[0], "height": output_size[1]},
                "common_valid_pixel_fraction": base_metrics["valid_pixel_fraction"],
                "common_valid_pixels": base_metrics["valid_pixels"],
                "common_valid_ssim_windows": base_metrics["valid_ssim_windows"],
                "baseline": {key: base_metrics[key] for key in ["psnr", "ssim"]},
                "candidate": {key: candidate_metrics[key] for key in ["psnr", "ssim"]},
            }
        )
    means = {
        mode: {
            metric: float(np.mean([row[mode][metric] for row in rows]))
            for metric in ["psnr", "ssim"]
        }
        for mode in ["baseline", "candidate"]
    }
    return {
        "method": "original_photo_domain",
        "camera_models": {"baseline": _models(baseline), "candidate": _models(candidate)},
        "baseline_job": baseline_id,
        "candidate_job": candidate_id,
        "requested_long_edge": size,
        "resampling": "raw reference Lanczos; render bilinear after COLMAP inverse distortion",
        "validity": "metrics use the intersection of both model-specific valid warp masks; SSIM uses only complete 11x11 windows inside it",
        "not_independent": "holdout photos still participated in upstream SfM",
        "views": rows,
        "mean_per_view": means,
        "candidate_minus_baseline": {
            metric: means["candidate"][metric] - means["baseline"][metric]
            for metric in ["psnr", "ssim"]
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(".data"))
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--images-dir", type=Path)
    args = parser.parse_args()
    result = compare(
        args.data, args.baseline, args.candidate, args.size, args.images_dir
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
