"""Prepare official ETH3D office / pipes photos for the local sample picker.

Only photographs are read; dataset camera calibration and models are not used.
"""

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps

from webscan.samples import SAMPLES


def prepare(archive: Path, scene: str, root: Path):
    sample = SAMPLES[f"eth3d-{scene}"]
    prefix = f"{scene}/images/dslr_images/"
    listing = subprocess.check_output(
        ["tar", "-tf", str(archive)], text=True
    ).splitlines()
    entries = sorted(
        name
        for name in listing
        if name.startswith(prefix) and name.lower().endswith(".jpg")
    )
    if len(entries) != sample.count or len(set(entries)) != sample.count:
        raise ValueError(
            f"预期 ETH3D {scene} 的 {sample.count} 张照片，请核对官方归档。"
        )
    if any(str(PurePosixPath(name).parent) + "/" != prefix for name in entries):
        raise ValueError("无效归档路径")
    root.mkdir(parents=True, exist_ok=True)
    output = root / sample.id
    with tempfile.TemporaryDirectory(prefix=f".{sample.id}-", dir=root) as temp:
        staged = Path(temp)
        files = []
        for name in entries:
            # Read bytes via stdout: archive paths and links are never extracted.
            raw = subprocess.check_output(["tar", "-xOf", str(archive), name])
            with Image.open(io.BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                target = staged / (PurePosixPath(name).stem + ".jpg")
                image.save(target, quality=95)
            files.append(
                {
                    "name": target.name,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        manifest = {**sample.provenance(), "files": files}
        (staged / "source.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        # Publish the completion marker last, so partial runs cannot be imported.
        output.mkdir(exist_ok=True)
        (output / "source.json").unlink(missing_ok=True)
        for item in staged.iterdir():
            if item.name != "source.json":
                shutil.copy2(item, output / item.name)
        shutil.copy2(staged / "source.json", output / "source.json")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Official <scene>_dslr_jpg.7z file")
    parser.add_argument(
        "--scene",
        choices=[sample.scene for sample in SAMPLES.values()],
        default="office",
    )
    args = parser.parse_args()
    output = prepare(
        args.archive.resolve(),
        args.scene,
        Path(__file__).resolve().parent.parent / ".samples",
    )
    print(f"照片已准备好，打开页面的「示例项目」即可导入：{output}")


if __name__ == "__main__":
    main()
