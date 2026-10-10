"""Create original textured-cube images for testing real, uncalibrated COLMAP.

Outputs images only: no camera parameters are supplied to the reconstruction job.
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from webscan.reconstruction import look_at


def generate(folder):
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(82)
    textures = []
    for face in range(7):
        image = Image.new("RGB", (768, 768), (160, 155, 142))
        draw = ImageDraw.Draw(image)
        for i in range(1800):
            x, y = rng.integers(0, 760, 2)
            r = int(rng.integers(2, 20))
            color = tuple(int(c) for c in rng.integers(20, 245, 3))
            draw.rectangle((x, y, x + r, y + r), fill=color)
        for i in range(80):
            x, y = rng.integers(0, 740, 2)
            draw.text((int(x), int(y)), f"{face}-{i}", fill=(20, 20, 20))
        textures.append(np.array(image))
    w, h = 640, 480
    f = 680
    yy, xx = np.meshgrid(np.arange(h) + 0.5, np.arange(w) + 0.5, indexing="ij")
    rays = np.stack(((xx - w / 2) / f, (yy - h / 2) / f, np.ones_like(xx)), -1)
    for index, angle in enumerate(np.linspace(-0.65, 0.65, 12)):
        camera = np.array([3.2 * np.sin(angle), -1.35, 3.2 * np.cos(angle)])
        view = look_at(camera)
        direction = rays @ view[:3, :3]
        nearest = np.full((h, w), np.inf)
        rgb = np.full((h, w, 3), 24, dtype="uint8")
        for axis in range(3):
            for side in [-1, 1]:
                t = (side * 0.72 - camera[axis]) / (direction[:, :, axis] + 1e-12)
                hit = camera + direction * t[:, :, None]
                other = [a for a in range(3) if a != axis]
                mask = (
                    (t > 0)
                    & (t < nearest)
                    & (np.abs(hit[:, :, other[0]]) <= 0.72)
                    & (np.abs(hit[:, :, other[1]]) <= 0.72)
                )
                u = np.clip(
                    ((hit[:, :, other[0]] / 0.72 + 1) * 0.5 * 767).astype(int), 0, 767
                )
                v = np.clip(
                    ((hit[:, :, other[1]] / 0.72 + 1) * 0.5 * 767).astype(int), 0, 767
                )
                rgb[mask] = textures[axis * 2 + (side == 1)][v[mask], u[mask]]
                nearest[mask] = t[mask]
        t = (0.85 - camera[1]) / (direction[:, :, 1] + 1e-12)
        hit = camera + direction * t[:, :, None]
        mask = (
            (t > 0)
            & (t < nearest)
            & (np.abs(hit[:, :, 0]) < 2.5)
            & (np.abs(hit[:, :, 2]) < 2.5)
        )
        u = np.mod((hit[:, :, 0] * 180).astype(int), 768)
        v = np.mod((hit[:, :, 2] * 180).astype(int), 768)
        rgb[mask] = textures[6][v[mask], u[mask]]
        Image.fromarray(rgb).save(folder / f"view-{index:02}.jpg", quality=97)
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    print(generate(parser.parse_args().output))
