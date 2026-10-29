from __future__ import annotations

import argparse
import json
import time
import traceback
import shutil
import subprocess
import sys
from pathlib import Path

from .store import Store


def run(root: Path, jid: str):
    store = Store(root)
    job = store.job(jid)
    session = store.session(job["session_id"])
    config = job["config"]
    work = store.job_dir(jid)
    work.mkdir(parents=True, exist_ok=True)
    source = store.session_dir(session["id"])
    started = time.monotonic()
    timings = {}
    sfm_reused = False
    snapshot_key = None

    def progress(stage, fraction, message):
        if store.job(jid)["status"] == "cancelled":
            raise InterruptedError("任务已取消")
        store.update(jid, stage=stage, progress=fraction, message=message)

    try:
        from .reconstruction import fixture_scene
        from .sfm_cache import (
            cache_key,
            pipeline_signature,
            validate_inputs,
            restore_snapshot,
            publish_snapshot,
        )

        progress("prepare", 0.03, "读取已冻结的采集数据")
        if session["kind"] == "fixture":
            scene = fixture_scene(source / "images", source / "fixture.json")
        else:
            manifest = json.loads((source / "manifest.json").read_text())
            phase = time.monotonic()
            validate_inputs(source, manifest)
            timings["input_validation_seconds"] = time.monotonic() - phase
            snapshot_key = cache_key(
                manifest,
                {
                    **pipeline_signature(),
                    "camera_mode": config.get("camera_mode", "auto"),
                },
            )
            cache = source / "cache" / "sfm" / snapshot_key
            phase = time.monotonic()
            sfm_reused = restore_snapshot(cache, snapshot_key, work / "sfm")
            if sfm_reused:
                progress("sfm", 0.36, "已复用校验通过的相机位姿与训练照片")
            else:
                images = work / "input"
                images.mkdir()
                for index, frame in enumerate(manifest["frames"]):
                    image = source / "images" / f"{frame['id']}.jpg"
                    shutil.copyfile(image, images / f"{index:04d}-{image.name}")
                progress("sfm", 0.08, "在独立进程中求解相机位姿")
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "webscan.sfm",
                        "--images",
                        str(images),
                        "--work",
                        str(work / "sfm"),
                        "--data",
                        str(store.root),
                        "--job",
                        jid,
                    ],
                    timeout=1800,
                )
                if result.returncode:
                    raise RuntimeError(
                        "COLMAP 未能恢复场景。请查看任务日志，检查照片重叠、纹理与清晰度。"
                    )
            timings["sfm_seconds"] = time.monotonic() - phase
            if not sfm_reused:
                progress("sfm", 0.38, "保存可复用的位姿结果")
                phase = time.monotonic()
                publish_snapshot(work / "sfm", cache, snapshot_key)
                timings["cache_publish_seconds"] = time.monotonic() - phase
            import numpy as np

            scene = json.loads((work / "sfm" / "scene.json").read_text())
            scene["points"] = np.array(scene["points"], np.float32)
            scene["colors"] = np.array(scene["colors"], np.float32)
        config = job["config"]
        progress("train", 0.42, "开始优化三维高斯")
        artifacts = work / "artifacts"
        phase = time.monotonic()
        if config["backend"] == "brush":
            from .brush import train

            report = train(scene, work, artifacts, config, progress)
        else:
            from .gaussian import train_scene

            report = train_scene(
                scene,
                artifacts,
                **{
                    k: v
                    for k, v in config.items()
                    if k not in ("camera_mode", "evaluation_mode")
                },
                progress=lambda step, total, loss: progress(
                    "train",
                    0.42 + 0.5 * step / total,
                    f"训练 {step}/{total} · loss {loss:.4f}",
                ),
            )
        timings["training_and_model_export_seconds"] = time.monotonic() - phase
        progress("export", 0.95, "导出模型与预览")
        warnings = []
        if scene["registered_images"] < scene["input_images"]:
            warnings.append(
                f"{scene['input_images'] - scene['registered_images']} 张照片未成功定位；缺失区域建议补拍。"
            )
        coverage = scene.get("sparse_view_coverage")
        if coverage and coverage.get("weak_views"):
            warnings.append(
                f"{len(coverage['weak_views'])} 张已定位照片的稀疏支撑偏低；"
                "如对应区域发软或有空洞，请补拍与相邻画面重叠的过渡视角。"
            )
        reprojection = scene.get("view_reprojection_coverage")
        if reprojection and reprojection.get("high_error_views"):
            warnings.append(
                f"{len(reprojection['high_error_views'])} 张已定位照片的相对重投影误差偏高；"
                "请结合原图与相邻视角复查，确认后再补拍重叠画面。"
            )
        spacing = scene.get("camera_spacing_coverage")
        if spacing and spacing.get("reason") == "coincident_camera_centers":
            warnings.append(
                "已定位相机的位置重合；仅转动镜头无法提供三维深度所需的视差，"
                "请围绕目标做小幅横移后补拍。"
            )
        elif spacing and spacing.get("isolated_views"):
            warnings.append(
                f"{len(spacing['isolated_views'])} 张已定位照片与最近相机的间隔偏大；"
                "请复查该位置是否有足够重叠的过渡照片。"
            )
        directions = scene.get("camera_direction_coverage")
        if directions and directions.get("narrow_coverage"):
            warnings.append(
                "多数已定位相机的朝向接近；请补拍侧向或反向视角，"
                "不要只沿同一朝向移动。"
            )
        image_detail = scene.get("image_detail_coverage")
        if image_detail and image_detail.get("reason") == "no_measurable_image_detail":
            warnings.append(
                "已定位照片几乎没有可测图像细节；请检查是否拍到纯色表面、"
                "失焦、抖动或严重曝光不足。"
            )
        elif image_detail and image_detail.get("low_detail_views"):
            warnings.append(
                f"{len(image_detail['low_detail_views'])} 张已定位照片的图像细节明显偏低；"
                "请复查是否为纯色区域、失焦、抖动或严重曝光问题。"
            )
        priority = scene.get("capture_review_priority")
        if priority and priority.get("priority_views"):
            warnings.append(
                f"{len(priority['priority_views'])} 张已定位照片同时出现稀疏支撑偏低和图像细节偏低；"
                "请优先结合原图复查，并按实际情况补拍相邻过渡视角。"
            )
        if scene.get("camera_mode_effective") not in (None, scene.get("camera_mode")):
            retry = scene.get("camera_mode_retry", {})
            auto = retry.get("auto", {})
            shared = retry.get("shared", {})
            warnings.append(
                "自动模式改用同镜头内参："
                f"定位 {auto.get('registered_images', 0)}/{auto.get('input_images', 0)}"
                f" → {shared.get('registered_images', 0)}/{shared.get('input_images', 0)}。"
            )
        report.update(
            seconds=round(time.monotonic() - started, 2),
            pose_source=scene["pose_source"],
            normalization=scene["normalization"],
            registered_images=scene["registered_images"],
            input_images=scene["input_images"],
            calibrated_fixture=session["kind"] == "fixture",
            camera_mode=scene.get("camera_mode", "auto"),
            camera_mode_effective=scene.get("camera_mode_effective"),
            camera_model=scene.get("camera_model"),
            camera_mode_retry=scene.get("camera_mode_retry"),
            matching_seed=scene.get("matching_seed"),
            sparse_points=scene.get("sparse_points"),
            reprojection_error=scene.get("reprojection_error"),
            unregistered_images=scene.get("unregistered_images", []),
            sparse_view_coverage=coverage,
            view_reprojection_coverage=reprojection,
            camera_spacing_coverage=spacing,
            camera_direction_coverage=directions,
            image_detail_coverage=image_detail,
            capture_review_priority=priority,
            warnings=warnings,
            sfm_reused=sfm_reused,
            sfm_cache_key=snapshot_key,
            timings={name: round(value, 4) for name, value in timings.items()},
        )
        if (source / "source.json").exists():
            report["source"] = json.loads((source / "source.json").read_text())
        import numpy as np

        matrix = np.array(scene["views"][0]["view"])
        camera_position = (-matrix[:3, :3].T @ matrix[:3, 3]).tolist()
        (artifacts / "cameras.json").write_text(
            json.dumps(
                {
                    "position": camera_position,
                    "target": [0, 0, 0],
                    "coordinate_system": "COLMAP right-down-forward",
                    "views": [
                        {k: v for k, v in x.items() if k != "image"}
                        for x in scene["views"]
                    ],
                }
            )
        )
        (artifacts / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        store.update(
            jid,
            stage="complete",
            progress=1,
            message="模型已生成",
            status="succeeded",
            report=report,
        )
    except InterruptedError:
        return
    except Exception as error:
        traceback.print_exc()
        store.update(
            jid, stage="failed", progress=0, message=str(error)[:1500], status="failed"
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    run(args.data, args.job)
