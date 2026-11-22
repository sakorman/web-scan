import struct
import pytest
from scripts.benchmark_track_filter import filter_points, clear_associations, point_records, prepare


def point(pid, track):
    return struct.pack('<QdddBBBdQ',pid,1,2,3,17,42,254,.25,len(track))+b''.join(struct.pack('<II',*e) for e in track)


def images(ids):
    header=struct.pack('<Q',1)+struct.pack('<IdddddddI',4,1,0,0,0,1,2,3,1)+b'a.jpg\0'
    return header+struct.pack('<Q',len(ids))+b''.join(struct.pack('<ddq',.5+i,3.5,pid) for i,pid in enumerate(ids))


def test_filter_preserves_exact_records_order_and_pose_bytes():
    a=point(90,[(1,0),(2,1)])
    b=point(7,[(1,1),(1,2),(3,0)])
    c=point(3,[(2,0),(3,1)])
    raw=struct.pack('<Q',3)+a+b+c
    out, rejected=filter_points(raw)
    assert rejected==[7]
    assert out==struct.pack('<Q',2)+a+c  # No sorting, coordinate/color/error rewrite.
    before=images([90,7,-1,3,7])
    after,n=clear_associations(before,rejected)
    assert n==2
    assert after==images([90,-1,-1,3,-1])  # XY, names and poses unchanged.
    assert filter_points(out)==(out,[])
    assert clear_associations(after,[])[0]==after


@pytest.mark.parametrize('kind',['short_header','short_track','trailing','duplicate'])
def test_malformed_points_fail(kind):
    good=point(1,[(1,0),(2,1)])
    data={'short_header':b'123','short_track':struct.pack('<Q',1)+good[:-1],
          'trailing':struct.pack('<Q',1)+good+b'0','duplicate':struct.pack('<Q',2)+good+good}[kind]
    with pytest.raises(ValueError):list(point_records(data))


@pytest.mark.parametrize('data',[b'',images([1])[:-1],images([1])+b'0'])
def test_malformed_images_fail(data):
    with pytest.raises(ValueError):clear_associations(data,{1})


def test_source_and_existing_results_are_preserved(tmp_path):
    source=tmp_path/'source';source.mkdir()
    points=struct.pack('<Q',1)+point(1,[(1,0),(1,1)])
    (source/'points3D.bin').write_bytes(points)
    (source/'images.bin').write_bytes(images([1,1]))
    (source/'cameras.bin').write_bytes(b'camera untouched')
    destination=tmp_path/'candidate'
    assert prepare(source,destination)['retained_points']==0
    assert (source/'points3D.bin').read_bytes()==points
    assert (destination/'cameras.bin').read_bytes()==b'camera untouched'
    with pytest.raises(ValueError):prepare(source,destination)
    with pytest.raises(ValueError):prepare(source,source/'nested')
