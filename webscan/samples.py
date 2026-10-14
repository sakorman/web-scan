"""Known public photo sets. Images are prepared locally, never bundled with code."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PhotoSample:
    scene: str
    title: str
    description: str
    count: int

    @property
    def id(self):
        return f"eth3d-{self.scene}"

    def provenance(self):
        return {
            "name": f"ETH3D {self.scene}",
            "url": "https://www.eth3d.net/datasets",
            "archive_url": f"https://www.eth3d.net/data/{self.scene}_dslr_jpg.7z",
            "authors": "Schöps et al., ETH3D, CVPR 2017",
            "license": "CC BY-NC-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            "images": self.count,
            "type": "real indoor multiview dataset",
            "preprocessing": "EXIF orientation; resize to 1600px long edge; JPEG quality 95; preserve valid 35mm focal-length hint",
        }


SAMPLES = {
    sample.id: sample
    for sample in (
        PhotoSample("office", "办公室", "桌椅、显示器和窗边，先用这组熟悉重建。", 26),
        PhotoSample(
            "pipes", "管道房", "绕着管线拍摄的一组近景，适合观察细小结构。", 14
        ),
    )
}


def prepared_photos(root: Path, sample: PhotoSample) -> list[Path]:
    """A directory alone is not a ready dataset; require a completed manifest."""
    folder = root / sample.id
    try:
        manifest = json.loads((folder / "source.json").read_text())
        files = manifest["files"]
        if (
            manifest.get("name") != f"ETH3D {sample.scene}"
            or len(files) != sample.count
        ):
            return []
        names = [entry["name"] for entry in files]
        if len(set(names)) != sample.count or any(
            not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".jpg")
            for name in names
        ):
            return []
        photos = [folder / name for name in names]
        if folder.is_symlink() or any(
            photo.is_symlink() or not photo.is_file() or photo.stat().st_size == 0
            for photo in photos
        ):
            return []
        return photos
    except (OSError, ValueError, KeyError, TypeError):
        return []


def sample_info(root: Path, sample: PhotoSample):
    photos = prepared_photos(root, sample)
    return {
        "id": sample.id,
        "title": sample.title,
        "description": sample.description,
        **sample.provenance(),
        "available": bool(photos),
        "previews": [
            f"/api/samples/{sample.id}/photos/{index}"
            for index in (0, len(photos) // 2, len(photos) - 1)
        ]
        if photos
        else [],
        "prepare_command": f".venv/bin/python scripts/prepare_indoor.py /tmp/{sample.scene}_dslr_jpg.7z --scene {sample.scene}",
    }
