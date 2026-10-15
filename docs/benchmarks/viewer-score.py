"""Score saved 512px browser renders against the same undistorted photos.

Run from the repository root with the render directory as the first argument.
The job IDs refer to the fixed ETH3D comparison described in VIEWER-PRECISION.md.
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

repo = Path.cwd()
sys.path.insert(0, str(repo))
from scripts.compare_quality import metrics

root = Path(sys.argv[1])
torch.set_num_threads(4)
results = []
for scene, jid, views in [
    ("pipes", "82acea65e9874f64806815a592015dec", [0, 8]),
    ("office", "59bb5740d2e945d9a459ca2514c66bb2", [0, 8, 16, 24]),
]:
    job = repo / ".data/jobs" / jid
    camera_path = job / "artifacts/cameras.json"
    cameras = json.loads(camera_path.read_text())
    rows = []
    for index in views:
        name = cameras["views"][index]["image_name"]
        path = job / "sfm/undistorted/images" / name
        row = {
            "index": index,
            "reference_name": name,
            "reference_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        size = None
        for mode in ["baseline", "extended"]:
            render = root / f"{scene}-{index}-{mode}-app.png"
            with Image.open(render) as source:
                image = source.convert("RGB")
            if size is None:
                size = image.size
            assert image.size == size and image.width == 512
            with Image.open(path) as source:
                reference = source.convert("RGB").resize(size, Image.Resampling.LANCZOS)
            row[mode] = metrics(image, reference)
            row[mode + "_sha256"] = hashlib.sha256(render.read_bytes()).hexdigest()
        row.update(width=size[0], height=size[1])
        rows.append(row)
    result = {
        "scene": scene,
        "job_id": jid,
        "model_sha256": hashlib.sha256(
            (job / "artifacts/gaussians.ply").read_bytes()
        ).hexdigest(),
        "camera_sha256": hashlib.sha256(camera_path.read_bytes()).hexdigest(),
        "rows": rows,
        "mean": {
            mode: {
                key: float(np.mean([row[mode][key] for row in rows]))
                for key in ["psnr", "ssim"]
            }
            for mode in ["baseline", "extended"]
        },
    }
    results.append(result)
    print(json.dumps({"scene": scene, "mean": result["mean"]}))
(root / "metrics-app.json").write_text(json.dumps(results, indent=2) + "\n")
