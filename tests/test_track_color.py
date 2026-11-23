from collections import defaultdict
import numpy as np
import pytest
from scripts.benchmark_track_color import sample_rgb, training_pairs, summarize_pairs


def test_colmap_half_pixel_and_bilinear_ramp():
    pixels=np.zeros((3,3,3),dtype=np.uint8)
    pixels[0,1]=[100,0,0];pixels[1,0]=[0,100,0];pixels[1,1]=[0,0,100]
    assert sample_rgb(pixels,[0.5,0.5])==pytest.approx([0,0,0])
    assert sample_rgb(pixels,[1,1])==pytest.approx(np.array([25,25,25])/255)


@pytest.mark.parametrize('xy',[[0,1],[3,1],[1,3],[np.nan,1],[1,np.inf]])
def test_sampling_rejects_outside_and_nonfinite(xy):
    assert sample_rgb(np.zeros((3,3,3),dtype=np.uint8),xy) is None


def test_holdout_colors_never_affect_training_deltas():
    a=np.array([0.2,0.3,0.4]);b=a+[0.1,-0.1,0]
    pairs=list(training_pairs([(1,a),(2,b),(3,[100,100,100])],{1,2}))
    assert pairs[0][0]==(1,2)
    assert pairs[0][1]==pytest.approx([0.1,-0.1,0])
    assert len(pairs)==1
    assert list(training_pairs([(1,a),(3,b)],{1,2}))==[]


def test_duplicate_camera_does_not_increase_support():
    with pytest.raises(ValueError,match='Duplicate'):
        list(training_pairs([(1,[0,0,0]),(1,[1,1,1]),(2,[0,0,0])],{1,2}))


def test_constant_color_offset_and_minimum_support():
    pairs=defaultdict(list)
    for _ in range(20):pairs[(1,2)].append([0.1,-0.1,0])
    pairs[(1,3)]=[[1,1,1]]
    r=summarize_pairs(pairs,{1:'a',2:'b',3:'c'})
    assert r['eligible_pairs']==1
    assert r['median_of_pair_median_abs_rgb']==pytest.approx(0.2/3)
    assert r['pairs'][0]['median_rgb_delta']==pytest.approx([0.1,-0.1,0])
    assert not r['pairs'][1]['eligible']
    assert summarize_pairs({}, {})['median_of_pair_median_abs_rgb'] is None


def test_opposite_local_changes_are_not_hidden_by_signed_median():
    r=summarize_pairs({(1,2):[[0.2,0,0]]*10+[[-0.2,0,0]]*10},{1:'a',2:'b'})
    assert r['pairs'][0]['median_rgb_delta']==pytest.approx([0,0,0])
    assert r['pairs'][0]['median_abs_rgb']==pytest.approx(0.2/3)


def test_measure_rejects_ambiguous_tracks_and_never_decodes_holdout(tmp_path, monkeypatch):
    import hashlib
    import json
    import sys
    from types import SimpleNamespace as NS
    from PIL import Image
    from scripts.benchmark_track_color import measure

    class Pose:
        def matrix(self):
            return np.eye(4)[:3]

        def __mul__(self, xyz):
            return xyz

    folder = tmp_path/'jobs'/'fixture'/'sfm'/'undistorted'
    (folder/'images').mkdir(parents=True)
    (folder/'sparse').mkdir()
    artifacts = tmp_path/'jobs'/'fixture'/'artifacts'
    artifacts.mkdir()
    camera = NS(model=NS(name='PINHOLE'), width=3, height=3,
                calibration_matrix=lambda: np.eye(3))
    images, views, refs = {}, [], []
    for i in range(9):
        name = f'{i:04d}.png'
        path = folder/'images'/name
        Image.fromarray(np.full((3,3,3), i*20, dtype=np.uint8)).save(path)
        images[i] = NS(name=name, camera_id=1, cam_from_world=lambda: Pose(), points2D=[])
        views.append(dict(image_name=name, width=3, height=3, view=np.eye(4).tolist(), K=np.eye(3).tolist()))
        if i%8 == 0:
            refs.append(dict(name=name, reference_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    points = {}
    # A valid match, an ambiguous duplicate, a point behind the camera,
    # and a feature with excessive per-observation reprojection error.
    for pid, ids, xyz, xy in [(1,[1,2,8],[1,1,1],[1,1]), (2,[1,1,2,3],[1,1,1],[1,1]),
                              (3,[1,2],[1,1,-1],[1,1]), (4,[1,2],[10,10,1],[1,1]),
                              (5,[1,2,8,8],[1,1,1],[1,1])]:
        elements = []
        for iid in ids:
            elements.append(NS(image_id=iid, point2D_idx=len(images[iid].points2D)))
            images[iid].points2D.append(NS(point3D_id=pid, xy=np.array(xy), has_point3D=lambda: True))
        points[pid] = NS(track=NS(elements=elements), error=0, xyz=np.array(xyz))
    model = NS(images=images, cameras={1:camera}, points3D=points)
    monkeypatch.setitem(sys.modules, 'pycolmap', NS(Reconstruction=lambda _:model, __version__='fixture'))
    (folder.parent/'scene.json').write_text(json.dumps(dict(views=views, normalization=dict(center=[0,0,0],scale=1))))
    report = dict(evaluation_mode='holdout', evaluation_verified=True, training_views=7,
                  evaluation_views=2, evaluation_images=refs)
    report_path = artifacts/'report.json'
    report_path.write_text(json.dumps(report))
    original_open = Image.open
    opened = []

    def training_open(path):
        assert path.name not in ('0000.png','0008.png'), 'Holdout decoded for color measurement'
        opened.append(path.name)
        return original_open(path)

    monkeypatch.setattr(Image, 'open', training_open)
    result = measure(tmp_path, 'fixture', duplicate_policy='reject-track')
    assert len(opened) == 7
    assert result['counts']['accepted_tracks'] == 1
    assert result['counts']['ambiguous_camera_track_rejected'] == 2
    assert result['counts']['behind_camera'] == 2
    assert result['counts']['observation_error_rejected'] == 2
    pair = result['focus_training']['pairs'][0]
    assert pair['tracks'] == 1
    assert pair['median_rgb_delta'] == pytest.approx([20/255]*3)
    updated = measure(tmp_path, 'fixture')
    assert updated['settings']['duplicate_policy'] == 'exclude-image'
    assert updated['counts']['accepted_tracks'] == 3
    assert updated['counts']['recovered_tracks'] == 2
    assert updated['counts']['ambiguous_training_images_excluded'] == 1
    assert updated['counts']['ambiguous_training_observations_excluded'] == 2
    assert updated['ambiguous_focus_tracks_excluded'] == 1
    assert [p['point_id'] for p in updated['focus_points']] == [1]
    assert [p['image_name'] for p in updated['recovered_tracks'][0]['observations']] == ['0002.png','0003.png']
    assert updated['all_training']['pairs'][0]['tracks'] == 2
    assert updated['all_training']['pairs'][1]['tracks'] == 1
    assert updated['all_training']['pairs'][1]['median_rgb_delta'] == pytest.approx([20/255]*3)
    assert updated['focus_training'] == result['focus_training']
    # Input track order cannot change which clean observations contribute.
    for point in points.values():point.track.elements.reverse()
    again = measure(tmp_path, 'fixture')
    for key in ('counts','recovered_tracks','all_training','focus_training','focus_points'):
        assert again[key] == updated[key]
    report['evaluation_images'][0]['reference_sha256'] = 'changed'
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='reference identity'):
        measure(tmp_path, 'fixture')


def test_ambiguous_image_is_excluded_even_for_identical_repeated_element():
    from types import SimpleNamespace as NS
    from scripts.benchmark_track_color import unique_camera_elements
    track = [NS(image_id=i,point2D_idx=j) for i,j in [(5,1),(2,0),(5,1),(7,0),(7,1)]]
    elements, ambiguous = unique_camera_elements(track)
    assert [(e.image_id,e.point2D_idx) for e in elements] == [(2,0)]
    assert ambiguous == {5,7}
