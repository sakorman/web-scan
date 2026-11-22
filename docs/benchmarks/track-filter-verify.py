"""Read back the filtered models with pycolmap; run separately from Torch."""
from pathlib import Path
import json,numpy as np,pycolmap
import argparse
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
args=p.parse_args()
root=args.root/'jobs'
result={}
for scene in ('pipes','office'):
 a=pycolmap.Reconstruction(root/f'{scene}-baseline-1/sfm/undistorted/sparse')
 b=pycolmap.Reconstruction(root/f'{scene}-candidate-1/sfm/undistorted/sparse')
 missing=set(a.points3D)-set(b.points3D)
 assert len(missing)==(21 if scene=='pipes' else 88)
 assert set(a.images)==set(b.images)
 for iid,im in b.images.items():
  old=a.images[iid]
  assert im.name==old.name and im.camera_id==old.camera_id
  np.testing.assert_array_equal(im.cam_from_world().matrix(),old.cam_from_world().matrix())
  assert len(im.points2D)==len(old.points2D)
  for oldp,newp in zip(old.points2D,im.points2D):
   np.testing.assert_array_equal(oldp.xy,newp.xy)
   if oldp.point3D_id in missing:assert not newp.has_point3D()
   else:assert oldp.point3D_id==newp.point3D_id
 for pid,p in b.points3D.items():
  np.testing.assert_array_equal(p.xyz,a.points3D[pid].xyz)
  np.testing.assert_array_equal(p.color,a.points3D[pid].color)
  ids=[e.image_id for e in p.track.elements];assert len(ids)==len(set(ids))
  for e in p.track.elements:assert b.images[e.image_id].points2D[e.point2D_idx].point3D_id==pid
 result[scene]=dict(original=a.num_points3D(),candidate=b.num_points3D(),removed=len(missing),
                    cameras_and_xy_exact=True,retained_xyz_rgb_exact=True,reciprocal_associations_valid=True)
 print(scene,result[scene])
args.output.write_text(json.dumps(result,indent=2))
