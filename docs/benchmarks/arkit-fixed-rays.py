"""Evaluate training depth on fixed source rays from a posed RGB reconstruction."""
import argparse
import io
import json
import os
import time
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pycolmap
from PIL import Image

from scripts.prepare_arkitscenes import indexed


def prepare(source, archive, target):
    t = time.monotonic()
    target.mkdir()
    for name in ("images", "sparse"):
        (target / name).mkdir()
    for photo in (source / "images").iterdir():
        os.link(photo, target / "images" / photo.name)
    for name in ("images.txt", "cameras.txt"):
        os.link(source / "sparse" / name, target / "sparse" / name)
    model = pycolmap.Reconstruction(source / "sparse")
    ordered = sorted(model.images.values(), key=lambda im: im.name)
    train = {im.image_id for i, im in enumerate(ordered) if i % 8}
    planes = {}
    moved = []
    pixel_shifts = []
    with ZipFile(archive) as z, (target/"sparse/points3D.txt").open("w") as points:
        names = indexed(z, ".png")
        for line in (source/"sparse/points3D.txt").read_text().splitlines():
            fields = line.split()
            iid = int(fields[8])
            assert iid in train
            im = model.images[iid]
            if iid not in planes:
                stamp = float(Path(im.name).stem.split("-")[1])
                planes[iid] = np.array(Image.open(io.BytesIO(z.read(names[stamp]))), dtype=float)/1000
            raw = planes[iid]
            xyz = np.array(fields[1:4], dtype=float)
            pose = im.cam_from_world()
            local = pose * xyz
            k = model.cameras[im.camera_id].calibration_matrix()
            uv = k @ local
            pixel = uv[:2]/uv[2]
            x, y = np.rint(pixel-.5).astype(int)
            neighborhood = raw[y-1:y+2, x-1:x+2]
            if (neighborhood.shape == (3,3) and np.all((neighborhood > .25) & (neighborhood < 5))
                    and np.max(abs(neighborhood-raw[y,x])) <= .05):
                updated = pose.inverse() * (local * (raw[y,x]/local[2]))
                reproj = k @ (pose * updated)
                pixel_shifts.append(float(np.linalg.norm(reproj[:2]/reproj[2]-pixel)))
                moved.append(float(np.linalg.norm(xyz-updated)))
                fields[1:4] = map(str, updated)
                # Report the new maximum track reprojection error without changing observations.
                errors = []
                for v, idx in zip(fields[8::2], fields[9::2], strict=True):
                    img = model.images[int(v)]
                    assert img.image_id in train
                    uv = model.cameras[img.camera_id].calibration_matrix() @ (img.cam_from_world()*updated)
                    errors.append(np.linalg.norm(uv[:2]/uv[2]-img.points2D[int(idx)].xy))
                fields[7] = str(max(errors))
            points.write(" ".join(fields)+"\n")
    assert max(pixel_shifts) < 1e-8
    return dict(seeds=model.num_points3D(), moved=len(moved), depths_read=len(planes),
                median_displacement_m=float(np.median(moved)), p90_displacement_m=float(np.quantile(moved,.9)),
                max_pixel_shift=max(pixel_shifts), seconds=time.monotonic()-t)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("source", "archive", "target"):
        parser.add_argument(flag, type=Path)
    args = parser.parse_args()
    result = prepare(args.source, args.archive, args.target)
    (args.target/"preparation.json").write_text(json.dumps(result,indent=2)+"\n")
    print(result)
