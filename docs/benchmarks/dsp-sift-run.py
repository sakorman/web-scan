"""Frozen experiment driver: one independent SfM + Brush run; keep failures."""
import argparse
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import time
from pathlib import Path

import pycolmap

from scripts.benchmark_exports import write_comparison_artifacts
from webscan.brush import train
from webscan.raw_inputs import resolve_raw_input


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    root = args.root.resolve()
    repo = args.repo.resolve()
    work = root / "jobs" / args.name
    work.mkdir(exist_ok=False)
    started = time.monotonic()
    result = {"name": args.name, "variant": args.variant, "source_job": args.source,
              "pycolmap": pycolmap.__version__, "status": "running"}
    stages = {}
    for name in ("extract_features", "match_exhaustive", "incremental_mapping", "undistort_images"):
        original = getattr(pycolmap, name)
        def measured(*a, _fn=original, _name=name, **kw):
            start = time.monotonic()
            try:
                return _fn(*a, **kw)
            finally:
                stages[_name] = time.monotonic() - start
        setattr(pycolmap, name, measured)
    try:
        source = repo / ".data/jobs" / args.source
        descriptor = json.loads((source / "source.json").read_text())
        shutil.copy2(source / "source.json", work / "source.json")
        (work / "input").mkdir()
        inputs = []
        for i, frame in enumerate(descriptor["frames"]):
            name = f"{i:04d}-{frame['id']}.jpg"
            path, digest, _ = resolve_raw_input(repo / ".data", source, name)
            shutil.copy2(path, work / "input" / name)
            inputs.append({"name": name, "sha256": digest, "width": frame["width"], "height": frame["height"]})
        result["inputs"] = inputs
        adapter = root / "adapters" / f"{args.variant}.py"
        result["adapter_sha256"] = sha(adapter)
        spec = importlib.util.spec_from_file_location("webscan.sfm_experiment", adapter)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sfm_start = time.monotonic()
        scene = module.estimate_scene(work / "input", work / "sfm", lambda *_: None, camera_mode="shared")
        result["sfm_seconds"] = time.monotonic() - sfm_start
        scene["points"] = scene["points"].tolist()
        scene["colors"] = scene["colors"].tolist()
        write_comparison_artifacts(work, scene, {})
        result["sfm"] = {k: scene[k] for k in ("input_images", "registered_images", "sparse_points", "reprojection_error", "unregistered_images", "camera_model", "camera_mode_effective")}
        result["sfm"]["original_cameras"] = list({json.dumps(v["original_camera"], sort_keys=True): v["original_camera"] for v in scene["views"]}.values())
        result["sfm"]["undistorted_sizes"] = sorted({(v["width"],v["height"]) for v in scene["views"]})
        with sqlite3.connect(f"file:{work / 'sfm/database.db'}?mode=ro", uri=True) as db:
            result["sfm"]["features"] = db.execute("SELECT sum(rows) FROM keypoints").fetchone()[0]
            result["sfm"]["verified_pairs"], result["sfm"]["verified_inliers"] = db.execute("SELECT count(*),sum(rows) FROM two_view_geometries WHERE rows>0").fetchone()
        config = json.loads((root / "config.json").read_text())["training"]
        training_start = time.monotonic()
        report = train(scene, work, work / "artifacts", config, lambda *_: None)
        result["training_export_seconds"] = time.monotonic() - training_start
        write_comparison_artifacts(work, scene, report)
        result["report"] = report
        result["model_bytes"] = (work / "artifacts/gaussians.ply").stat().st_size
        for row in inputs:
            assert sha(work / "input" / row["name"]) == row["sha256"]
        result["inputs_unchanged"] = True
        result["status"] = "complete"
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        result["stages_seconds"] = stages
        result["end_to_end_seconds"] = time.monotonic() - started
        (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({k:v for k,v in result.items() if k not in ("inputs", "report")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    run(p.parse_args())
