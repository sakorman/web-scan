"""Prepare an independently downloaded ETH3D office archive for the local sample UI.

Dataset: https://www.eth3d.net/datasets
Archive: https://www.eth3d.net/data/office_dslr_jpg.7z
Raw images and generated assets are never tracked in Git.
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageOps

parser = argparse.ArgumentParser()
parser.add_argument("archive", type=Path, help="Official office_dslr_jpg.7z file")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
output = root / ".samples/eth3d-office"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="webscan-eth3d-") as temp:
    # Read known image entries one-by-one through libarchive tar, avoiding path extraction.
    listing = subprocess.check_output(
        ["tar", "-tf", str(args.archive)], text=True
    ).splitlines()
    entries = [
        name
        for name in listing
        if name.startswith("office/images/dslr_images/")
        and name.lower().endswith(".jpg")
    ]
    if len(entries) != 26:
        raise SystemExit("预期 ETH3D office 的 26 张图片；请核对归档来源。")
    # Official archive paths have a fixed prefix, no symlinks or absolute paths are extracted.
    for name in entries:
        if ".." in Path(name).parts:
            raise SystemExit("无效归档路径")
    subprocess.run(["tar", "-xf", str(args.archive), "-C", temp, *entries], check=True)
    for name in entries:
        with Image.open(Path(temp) / name) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            image.save(output / (Path(name).stem + ".jpg"), quality=95)
(output / "source.json").write_text(
    json.dumps(
        {
            "dataset": "ETH3D office",
            "url": "https://www.eth3d.net/datasets",
            "images": 26,
        },
        indent=2,
    )
)
print(
    f"Prepared {len(entries)} images. Restart Web Scan to see the indoor sample: {output}"
)
