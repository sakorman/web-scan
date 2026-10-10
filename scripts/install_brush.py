"""Install a pinned official Brush binary, verifying its published SHA-256."""

import hashlib
import io
import platform
import shutil
import tarfile
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent.parent
targets = {
    ("Darwin", "arm64"): "aarch64-apple-darwin",
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
}
target = targets.get((platform.system(), platform.machine()))
if not target:
    raise SystemExit(
        "此脚本支持 Apple Silicon / Linux x86_64；其他平台请设置 WEBSCAN_BRUSH。"
    )
filename = f"brush-app-{target}.tar.xz"
base = "https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/"
print("Downloading Brush 0.3.0 from its official release…", flush=True)
with urllib.request.urlopen(base + filename, timeout=180) as response:
    blob = response.read(200 * 1024 * 1024)
with urllib.request.urlopen(base + filename + ".sha256", timeout=30) as response:
    checksum = response.read().decode().split()[0]
if hashlib.sha256(blob).hexdigest() != checksum:
    raise SystemExit("SHA-256 校验失败")
destination = root / ".tools/brush"
destination.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:xz") as archive:
    for entry in archive.getmembers():
        name = Path(entry.name).name
        if entry.isfile() and name in ("brush_app", "LICENSE", "README.md"):
            with (
                archive.extractfile(entry) as source,
                (destination / name).open("wb") as output,
            ):
                shutil.copyfileobj(source, output)
(destination / "brush_app").chmod(0o755)
print(f"Installed: {destination / 'brush_app'}")
