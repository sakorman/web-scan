import numpy as np
import pytest
from scripts.benchmark_gmdepth import network_intrinsics, relative_pose, choose_target


def test_resized_intrinsics_match_corner_ray_and_pillow_centers():
    k=np.array([[800.,0,300.],[0,900.,250.],[0,0,1.]])
    resized=network_intrinsics(k,[600,500],[512,432])
    ray=np.array([.2,-.1,1.])
    assert np.allclose(resized@ray,(k@ray)*[512/600,432/500,1]-[.5,.5,0])
    assert np.array_equal(k,[[800,0,300],[0,900,250],[0,0,1]])


def test_relative_pose_projects_world_point_to_target():
    source=np.eye(4);source[:3,3]=[1,2,3]
    target=np.eye(4);target[:3,:3]=[[0,-1,0],[1,0,0],[0,0,1]];target[:3,3]=[-2,0,1]
    world=np.array([4,5,6,1])
    assert np.allclose(relative_pose(source,target)@(source@world), target@world)


def test_pair_selection_rejects_rotation_and_uses_overlap_before_name():
    views=[]
    for name,x in [('source',0),('rotation',0),('a',.2),('b',.3)]:
        p=np.eye(4);p[0,3]=-x
        views.append(dict(name=name,pose=p))
    points={i:np.array([i*.001,0,3.]) for i in range(30)}
    tracks={v['name']:set(points) for v in views};tracks['a']=set(range(20))
    target,count,angle=choose_target(views[0],views,tracks,points)
    assert target['name']=='b' and count==30 and 1<=angle<=30
    with pytest.raises(ValueError,match='No training target'):
        choose_target(views[0],views[:2],tracks,points)
