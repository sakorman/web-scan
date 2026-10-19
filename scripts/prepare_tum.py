"""Prepare 96 RGB frames from the official TUM freiburg1_desk archive.

Depth, supplied calibration and ground-truth poses are never used for reconstruction.
"""

import argparse
import hashlib
import io
import json
import math
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from PIL import Image

from webscan.samples import SAMPLES

PREFIX = "rgbd_dataset_freiburg1_desk/rgb/"
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def rgb_entries(archive):
    names = []
    with tarfile.open(archive, "r|gz") as bundle:
        for entry in bundle:
            name = entry.name
            if not name.startswith(PREFIX):
                continue
            if entry.isdir() and name.rstrip("/") == PREFIX.rstrip("/"):
                continue
            if name != PREFIX + PurePosixPath(name).name or not name.endswith(".png"):
                raise ValueError("无效 RGB 归档路径")
            if not entry.isfile() or not 0 < entry.size <= MAX_IMAGE_BYTES:
                raise ValueError("RGB 图片必须是 8 MiB 以内的普通文件")
            timestamp = float(PurePosixPath(name).stem)
            if not math.isfinite(timestamp) or timestamp <= 0:
                raise ValueError("无效 RGB 时间戳")
            names.append((timestamp, name))
    if len(names) != len({timestamp for timestamp, _ in names}):
        raise ValueError("RGB 时间戳重复")
    return [name for _, name in sorted(names)]


def prepare(archive: Path, root: Path):
    sample = SAMPLES["tum-freiburg1_desk"]
    names = rgb_entries(archive)
    if len(names) < sample.count:
        raise ValueError(f"至少需要 {sample.count} 张 RGB 照片，请核对官方归档")
    selected = {
        names[round(i * (len(names) - 1) / (sample.count - 1))]
        for i in range(sample.count)
    }
    root.mkdir(parents=True, exist_ok=True)
    output = root / sample.id
    with tempfile.TemporaryDirectory(prefix=f".{sample.id}-", dir=root) as temp:
        staged = Path(temp)
        files = []
        with tarfile.open(archive, "r|gz") as bundle:
            for entry in bundle:
                if entry.name not in selected:
                    continue
                if not entry.isfile() or not 0 < entry.size <= MAX_IMAGE_BYTES:
                    raise ValueError("无效 RGB 照片")
                target = staged / (PurePosixPath(entry.name).stem + ".jpg")
                if target.exists():
                    raise ValueError("RGB 照片重复")
                with bundle.extractfile(entry) as photo:
                    raw = photo.read()
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != "PNG" or image.size != (640, 480):
                        raise ValueError("预期官方 640×480 PNG 照片")
                    image.convert("RGB").save(target, quality=95)
                files.append(
                    {
                        "name": target.name,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    }
                )
        if len(files) != sample.count:
            raise ValueError("RGB 照片不完整")
        files.sort(key=lambda item: float(Path(item["name"]).stem))
        manifest = {
            **sample.provenance(),
            "original_rgb_frames": len(names),
            "selection": "uniform rounded indices across timestamp-sorted RGB frames, including endpoints",
            "files": files,
        }
        (staged / "source.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        output.mkdir(exist_ok=True)
        (output / "source.json").unlink(missing_ok=True)
        for path in staged.glob("*.jpg"):
            shutil.copy2(path, output / path.name)
        shutil.copy2(staged / "source.json", output / "source.json")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    output = prepare(
        args.archive.resolve(), Path(__file__).resolve().parent.parent / ".samples"
    )
    print(f"照片已准备好：{output}")


if __name__ == "__main__":
    main()
