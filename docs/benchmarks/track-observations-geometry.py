"""Audit recovered color tracks using independent pycolmap readback; no pixel sampling."""
import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path

import numpy as np
import pycolmap

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--audit',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
args=p.parse_args();audit=json.loads(args.audit.read_text())
model=pycolmap.Reconstruction(Path('.data/jobs')/audit['job']/'sfm/undistorted/sparse')
images=sorted(model.images.items(),key=lambda x:x[1].name)
training={iid for i,(iid,_) in enumerate(images) if i%8};ids={im.name:iid for iid,im in images}
rows=[]
for row in audit['recovered_tracks']:
 point=model.points3D[row['point_id']];counts=Counter(e.image_id for e in point.track.elements)
 centers=[];residuals=[]
 assert sorted(model.images[i].name for i,n in counts.items() if n>1)==row['excluded_images']
 for obs in row['observations']:
  iid=ids[obs['image_name']];im=model.images[iid]
  assert iid in training and counts[iid]==1
  feature=im.points2D[obs['point2D_index']]
  assert feature.point3D_id==row['point_id']
  xyz=im.cam_from_world()*point.xyz
  assert xyz[2]>0
  residual=float(np.linalg.norm(model.cameras[im.camera_id].img_from_cam(xyz)-feature.xy))
  assert residual<=2 and abs(residual-obs['residual_px'])<1e-9
  centers.append(im.projection_center());residuals.append(residual)
 angles=[float(np.degrees(pycolmap.calculate_triangulation_angle(a,b,point.xyz))) for a,b in combinations(centers,2)]
 assert len(centers)>=2
 rows.append(dict(point_id=row['point_id'],clean_training_images=len(centers),
                  max_triangulation_angle_deg=max(angles),max_residual_px=max(residuals)))
result=dict(job=audit['job'],tracks=rows,verified=True,pycolmap=pycolmap.__version__,
            scope='Only recovered clean training observations; angle is descriptive, not a new acceptance threshold.')
args.output.write_text(json.dumps(result,indent=2)+'\n')
