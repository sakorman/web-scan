"""Prepare official ETH3D office / pipes photos for the local sample picker.

Only photographs are read; dataset camera calibration and models are not used.
"""

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps

from webscan.image_metadata import focal_length_exif
from webscan.samples import SAMPLES

MAX_PHOTO_BYTES = 64 * 1024 * 1024


@contextmanager
def read_archive(archive: Path):
    """Read each entry once, without extracting archive paths to disk."""
    with archive.open("rb") as source:
        is_7z = source.read(6) == b"7z\xbc\xaf\x27\x1c"
        if not is_7z:
            source.seek(0)
            with tarfile.open(fileobj=source, mode="r|*") as bundle:
                yield bundle
            return

    # libarchive imports solid 7z archives into a tar stream in one decode pass.
    # A temporary stderr file cannot fill a pipe while image bytes are consumed.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["tar", "-cf", "-", f"@{archive.resolve()}"],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as bundle:
                yield bundle
            # Consume tar padding before waiting for the producer to finish.
            while process.stdout.read(64 * 1024):
                pass
            if process.wait() != 0:
                errors.seek(0)
                detail = errors.read(4096).decode("utf-8", errors="replace").strip()
                raise ValueError(f"无法读取 7z 归档（需要 libarchive tar）：{detail}")
        finally:
            if process.poll() is None:
                process.terminate()
            process.stdout.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def prepare(archive: Path, scene: str, root: Path):
    sample = SAMPLES[f"eth3d-{scene}"]
    prefix = f"{scene}/images/dslr_images/"
    root.mkdir(parents=True, exist_ok=True)
    output = root / sample.id
    with tempfile.TemporaryDirectory(prefix=f".{sample.id}-", dir=root) as temp:
        staged = Path(temp)
        files = []
        names = set()
        with read_archive(archive) as bundle:
            for entry in bundle:
                name = entry.name
                if not name.startswith(prefix) or not name.lower().endswith(".jpg"):
                    continue
                if name != prefix + PurePosixPath(name).name:
                    raise ValueError("无效归档路径")
                if not entry.isfile() or not 0 < entry.size <= MAX_PHOTO_BYTES:
                    raise ValueError("归档照片必须是 64 MiB 以内的普通文件")
                target = staged / (PurePosixPath(name).stem + ".jpg")
                if target.name in names:
                    raise ValueError("归档包含重复照片")
                names.add(target.name)
                if len(names) > sample.count:
                    raise ValueError(f"预期 ETH3D {scene} 的 {sample.count} 张照片")
                with bundle.extractfile(entry) as photo:
                    raw = photo.read()
                with Image.open(io.BytesIO(raw)) as source:
                    focal_exif = focal_length_exif(source)
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                    image.save(target, quality=95, exif=focal_exif)
                files.append(
                    {
                        "name": target.name,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    }
                )
        if len(files) != sample.count:
            raise ValueError(
                f"预期 ETH3D {scene} 的 {sample.count} 张照片，请核对官方归档。"
            )
        files.sort(key=lambda item: item["name"])
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
        choices=[
            sample.scene for sample in SAMPLES.values() if sample.family == "eth3d"
        ],
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
