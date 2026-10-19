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
    family: str = "eth3d"
    collection: str = "ETH3D"
    url: str = "https://www.eth3d.net/datasets"
    download_url: str | None = None
    authors: str = "Schöps et al., ETH3D, CVPR 2017"
    license: str = "CC BY-NC-SA 4.0"
    license_url: str = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
    preprocessing: str = (
        "EXIF orientation; resize to 1600px long edge; JPEG quality 95; "
        "preserve valid 35mm focal-length hint"
    )

    @property
    def id(self):
        return f"{self.family}-{self.scene}"

    @property
    def name(self):
        return f"{self.collection} {self.scene}"

    @property
    def prepare_command(self):
        if self.family == "tum":
            return ".venv/bin/python scripts/prepare_tum.py /tmp/rgbd_dataset_freiburg1_desk.tgz"
        return f".venv/bin/python scripts/prepare_indoor.py /tmp/{self.scene}_dslr_jpg.7z --scene {self.scene}"

    def provenance(self):
        return {
            "name": self.name,
            "url": self.url,
            "archive_url": self.download_url
            or f"https://www.eth3d.net/data/{self.scene}_dslr_jpg.7z",
            "authors": self.authors,
            "license": self.license,
            "license_url": self.license_url,
            "images": self.count,
            "type": "real indoor multiview dataset",
            "preprocessing": self.preprocessing,
        }


SAMPLES = {
    sample.id: sample
    for sample in (
        PhotoSample("office", "办公室", "桌椅、显示器和窗边，先用这组熟悉重建。", 26),
        PhotoSample(
            "pipes", "管道房", "绕着管线拍摄的一组近景，适合观察细小结构。", 14
        ),
        PhotoSample(
            "freiburg1_desk",
            "连续桌面",
            "96 帧连续扫拍，640×480；只用彩色照片重建。",
            96,
            family="tum",
            collection="TUM RGB-D",
            url="https://cvg.cit.tum.de/data/datasets/rgbd-dataset",
            download_url="https://cvg.cit.tum.de/rgbd/dataset/freiburg1/rgbd_dataset_freiburg1_desk.tgz",
            authors="Sturm et al., TUM RGB-D Benchmark, IROS 2012",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            preprocessing="96 evenly spaced RGB frames in timestamp order; native 640x480; JPEG quality 95; no depth, supplied calibration or ground-truth poses",
        ),
    )
}


def prepared_photos(root: Path, sample: PhotoSample) -> list[Path]:
    """A directory alone is not a ready dataset; require a completed manifest."""
    folder = root / sample.id
    try:
        manifest = json.loads((folder / "source.json").read_text())
        files = manifest["files"]
        if manifest.get("name") != sample.name or len(files) != sample.count:
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
        "prepare_command": sample.prepare_command,
    }
