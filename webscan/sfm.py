"""COLMAP process boundary: its OpenMP runtime must not share a process with Torch."""

import argparse
import json
from pathlib import Path

from .reconstruction import estimate_scene
from .store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    store = Store(args.data)

    def progress(stage, fraction, message):
        store.update(args.job, stage=stage, progress=fraction, message=message)

    scene = estimate_scene(
        args.images,
        args.work,
        progress,
        store.job(args.job)["config"].get("camera_mode", "auto"),
    )
    result = {
        **scene,
        "points": scene["points"].tolist(),
        "colors": scene["colors"].tolist(),
    }
    (args.work / "scene.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
