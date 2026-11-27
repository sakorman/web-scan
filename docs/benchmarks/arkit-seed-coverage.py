"""Compare detected and spatially distributed depth samples with identical frame budgets."""
import argparse
import io
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pycolmap
from PIL import Image
from scripts.prepare_arkitscenes import indexed


def stable_depth(raw):
    valid = (raw > .25) & (raw < 5.)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            adjacent = np.roll(raw, (dy,dx), axis=(0,1))
            valid &= (adjacent > .25) & (adjacent < 5.) & (abs(adjacent-raw) <= .05)
    valid[[0,-1],:] = False
    valid[:,[0,-1]] = False
    return valid


def spatial_samples(valid, count):
    # Farthest-point sampling on valid native pixel centers, starting closest to the image center.
    y, x = np.nonzero(valid)
    centers = np.column_stack([x, y]).astype(np.int32)
    first = np.array(valid.shape[::-1]) // 2
    index = np.argmin(np.sum((centers-first)**2, axis=1))
    minimum = np.full(len(centers), np.iinfo(np.int32).max)
    chosen = []
    for _ in range(count):
        center = centers[index]
        chosen.append(center)
        residual = centers - center
        minimum = np.minimum(minimum, np.einsum("ij,ij->i", residual, residual))
        index = np.argmax(minimum)
    return np.array(chosen)


def prepare(source, assets, destination):
    model = pycolmap.Reconstruction(source/"sparse")
    header = (source/"sparse/images.txt").read_text().splitlines()[::2]
    indexed_names = {int(h.split()[0]):h.split()[-1] for h in header}
    heldout = {iid for j, (iid, _) in enumerate(sorted(indexed_names.items(), key=lambda p:p[1])) if j%8 == 0}
    coordinates = defaultdict(list)
    for line in (source/"sparse/points3D.txt").read_text().splitlines():
        fields = line.split()
        iid = int(fields[8])
        assert iid not in heldout
        im = model.images[iid]
        point = im.cam_from_world() * np.array(fields[1:4], dtype=float)
        uv = model.cameras[im.camera_id].calibration_matrix() @ point
        coordinates[iid].append(tuple(np.rint(uv[:2]/uv[2]-.5).astype(int)))
    results = {}
    for method in ("features", "spatial"):
        start = time.monotonic()
        out = destination/method
        out.mkdir()
        sparse = out/"sparse"
        sparse.mkdir()
        (out/"images").mkdir()
        for im in model.images.values():
            os.link(source/"images"/im.name, out/"images"/im.name)
        os.link(source/"sparse/cameras.txt", sparse/"cameras.txt")
        pid = 0
        by_frame = {}
        with ZipFile(assets/"lowres_depth.zip") as archive, (sparse/"images.txt").open("w") as views, (sparse/"points3D.txt").open("w") as points:
            lookup = indexed(archive, ".png")
            for h in header:
                iid = int(h.split()[0])
                im = model.images[iid]
                views.write(h+"\n")
                if iid not in coordinates:
                    views.write("\n")
                    continue
                assert iid not in heldout
                stamp = float(Path(im.name).stem.split("-")[1])
                raw = np.asarray(Image.open(io.BytesIO(archive.read(lookup[stamp]))),dtype=float)/1000
                ok = stable_depth(raw)
                found = list(dict.fromkeys((x,y) for x,y in coordinates[iid] if 0<=y<ok.shape[0] and 0<=x<ok.shape[1] and ok[y,x]))
                if not found:
                    views.write("\n")
                    continue
                xy = np.array(found) if method=="features" else spatial_samples(ok,len(found))
                cam = model.cameras[im.camera_id]
                grid = xy*np.array([8,6])//[cam.width,cam.height]
                by_frame[iid] = dict(points=len(xy),tiles=len(set(map(tuple,grid))))
                rgb = np.asarray(Image.open(out/"images"/im.name).convert("RGB"))
                K = cam.calibration_matrix()
                rays = np.column_stack([xy+.5,np.ones(len(xy))]) @ np.linalg.inv(K).T
                local = rays * raw[xy[:,1],xy[:,0],None]
                pose = im.cam_from_world()
                xyz = (local-pose.translation) @ pose.rotation.matrix()
                obs = []
                for idx, ((x,y), world) in enumerate(zip(xy,xyz,strict=True)):
                    pid += 1
                    points.write(" ".join(map(str,[pid,*world,*rgb[y,x],0,iid,idx]))+"\n")
                    obs.extend([str(x+.5),str(y+.5),str(pid)])
                views.write(" ".join(obs)+"\n")
        results[method] = dict(points=pid,occupied_tiles=sum(v["tiles"] for v in by_frame.values()),by_frame=by_frame,
                               prep_seconds=time.monotonic()-start)
    assert [v["points"] for v in results["features"]["by_frame"].values()] == [v["points"] for v in results["spatial"]["by_frame"].values()]
    (destination/"sample.json").write_text(json.dumps(results,indent=2)+"\n")
    print([(k,v["points"],v["occupied_tiles"],v["prep_seconds"]) for k,v in results.items()])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "assets", "destination"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    args.destination.mkdir()
    prepare(args.source, args.assets, args.destination)
