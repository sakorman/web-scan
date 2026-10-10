from __future__ import annotations

import argparse
import json
import time
import traceback
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from .store import Store


def run(root: Path, jid: str):
    store = Store(root)
    job = store.job(jid)
    session = store.session(job["session_id"])
    work = store.job_dir(jid)
    work.mkdir(parents=True, exist_ok=True)
    source = store.session_dir(session["id"])
    started = time.monotonic()

    def progress(stage, fraction, message):
        if store.job(jid)["status"] == "cancelled":
            raise InterruptedError("任务已取消")
        store.update(jid, stage=stage, progress=fraction, message=message)

    try:
        from .reconstruction import fixture_scene
        from .gaussian import train_scene

        progress("prepare", 0.03, "读取已冻结的采集数据")
        if session["kind"] == "fixture":
            scene = fixture_scene(source / "images", source / "fixture.json")
        else:
            manifest = json.loads((source / "manifest.json").read_text())
            images = work / "input"
            images.mkdir()
            for index, frame in enumerate(manifest["frames"]):
                image = source / "images" / f"{frame['id']}.jpg"
                if hashlib.sha256(image.read_bytes()).hexdigest() != frame["sha"]:
                    raise RuntimeError("采集文件校验失败，照片已发生变化；请重新采集。")
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
            import numpy as np

            scene = json.loads((work / "sfm" / "scene.json").read_text())
            scene["points"] = np.array(scene["points"], np.float32)
            scene["colors"] = np.array(scene["colors"], np.float32)
        config = job["config"]
        progress("train", 0.42, "开始优化三维高斯")
        artifacts = work / "artifacts"
        if config["backend"] == "brush":
            from .brush import train

            report = train(scene, work, artifacts, config, progress)
        else:
            report = train_scene(
                scene,
                artifacts,
                **config,
                progress=lambda step, total, loss: progress(
                    "train",
                    0.42 + 0.5 * step / total,
                    f"训练 {step}/{total} · loss {loss:.4f}",
                ),
            )
        progress("export", 0.95, "导出模型与预览")
        report.update(
            seconds=round(time.monotonic() - started, 2),
            pose_source=scene["pose_source"],
            normalization=scene["normalization"],
            registered_images=scene["registered_images"],
            input_images=scene["input_images"],
            calibrated_fixture=session["kind"] == "fixture",
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
