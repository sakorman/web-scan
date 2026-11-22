"""Trace duplicate-image tracks through preserved mapping, export and cache stages.

Run separately from Torch. Requires the preserved local ETH3D benchmark jobs.
"""
import argparse
from collections import defaultdict
from itertools import combinations
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pycolmap

SOURCES={'pipes':('6ea72a6d027348f6ae89252a7d088cae','5e45df7c1bfa4af6ba342326ca2fe407'),
         'office':('48915f24aab048bc8c91dbf80d721811','4533f0aa28a448bd9afb5983e6472de2')}


def inspect(path):
    m=pycolmap.Reconstruction(path)
    duplicates=[];tracks={};positions={}
    for pid,point in sorted(m.points3D.items()):
        groups=defaultdict(list)
        track=sorted((e.image_id,e.point2D_idx) for e in point.track.elements)
        tracks[pid]=track;positions[pid]=point.xyz.tolist()
        for iid,index in track:groups[iid].append(index)
        for iid,indices in sorted(groups.items()):
            if len(indices)<2:continue
            xy=[m.images[iid].points2D[i].xy.tolist() for i in indices]
            distances=[float(np.linalg.norm(np.array(a)-b)) for a,b in combinations(xy,2)]
            duplicates.append(dict(point_id=pid,image=m.images[iid].name,indices=indices,
                                   xy=xy,max_separation_px=max(distances)))
    return dict(points=m.num_points3D(),registered=m.num_reg_images(),
                duplicate_tracks=len({d['point_id'] for d in duplicates}),groups=duplicates,
                file_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.glob('*.bin'))}),tracks,positions


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();start=time.perf_counter();result={}
    for name,(original,cached) in SOURCES.items():
        root=Path('.data/jobs')/original/'sfm';stages={};identities=[]
        for label,path in [('mapped',root/'sparse/0'),('chosen',root/'chosen'),('undistorted',root/'undistorted/sparse'),
                           ('cached',Path('.data/jobs')/cached/'sfm/undistorted/sparse')]:
            stages[label],tracks,positions=inspect(path);identities.append((tracks,positions))
        assert all(i==identities[0] for i in identities)
        assert stages['undistorted']['file_sha256']==stages['cached']['file_sha256']
        result[name]=dict(original_job=original,cached_job=cached,stages=stages,
                          tracks_and_xyz_unchanged=True,cache_files_identical=True)
        print(name,stages['mapped']['duplicate_tracks'],'duplicate tracks present before undistortion')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(scenes=result,seconds=time.perf_counter()-start,pycolmap=pycolmap.__version__),indent=2)+'\n')


if __name__=='__main__':main()
