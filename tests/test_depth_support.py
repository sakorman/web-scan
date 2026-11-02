import json
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.benchmark_depth_prior import digest, save
from scripts.benchmark_depth_support import evaluate, selection_stats, unique_rows


@pytest.mark.parametrize('case', ['analytic', 'pinhole', 'geometry', 'invalid'])
def test_radial_geometry_in_isolated_native_process(case):
    # PyCOLMAP and PyTorch have separate native runtimes on macOS.
    code = '''
import numpy as np
from scripts.benchmark_depth_support import radial_camera, raw_project, raw_unproject
from scripts.benchmark_depth_consistency import check_pair, project, unproject
view={'camera_model':'RADIAL','camera_params':[100.,32.5,32.5,.1,-.02],
      'width':64,'height':64,'cam_from_world':np.column_stack([np.eye(3),np.zeros(3)]).tolist()}
a=radial_camera(view)
xy=np.array([[.5,.5],[32.5,32.5],[63.5,63.5]])
z=np.full(3,5.)
config={'relative_depth_tolerance':.05,'roundtrip_pixels':2.,'min_parallax_degrees':1.}
'''
    cases = {
        'analytic': '''
p=np.array([[.5,.2,2.],[1.,-.5,3.]])
n=p[:,:2]/p[:,2,None]; r2=(n*n).sum(axis=1)
expected=100*n*(1+.1*r2-.02*r2*r2)[:,None]+32.5
assert np.allclose(raw_project(p,a)[0],expected,atol=1e-10)
assert np.allclose(raw_project(raw_unproject(xy,z,a),a)[0],xy,atol=1e-8)
''',
        'pinhole': '''
a=radial_camera({**view,'camera_params':[100,32.5,32.5,0,0]})
b=(np.array([100,100,32.5,32.5]),a[1])
p=raw_unproject(xy,z,a)
assert np.allclose(p,unproject(xy,z,b),atol=1e-12)
assert np.allclose(raw_project(p,a)[0],project(p,b)[0],atol=1e-12)
''',
        'geometry': '''
b=(a[0],a[1].copy()); b[1][0,3]=-.5
xy=np.array([[32.5,32.5],[42.5,32.5]]);z=np.full(2,5.)
kwargs={'project_fn':raw_project,'unproject_fn':raw_unproject}
for depth,key in [(5.,'supported'),(4.,'occlusion_candidate'),(6.,'front_depth_conflict')]:
 assert check_pair(xy,z,a,b,np.full((64,64),depth),config,**kwargs)[key].all()
assert check_pair(xy,z,a,a,np.full((64,64),5.),config,**kwargs)['low_parallax'].all()
''',
        'invalid': '''
assert np.isnan(raw_project(np.array([[1,2,-1],[np.nan,2,1]]),a)[0]).all()
assert np.isnan(raw_unproject(np.array([[np.nan,1],[2,3]]),np.array([5,-1]),a)).all()
for change in [{'camera_model':'PINHOLE'},{'camera_params':[0,0,0,0,0]},
               {'cam_from_world':np.zeros((3,4)).tolist()}]:
 try: radial_camera({**view,**change})
 except ValueError: pass
 else: raise AssertionError('Invalid calibration accepted')
''',
    }
    subprocess.run([sys.executable, '-c', code + cases[case]], check=True, capture_output=True, text=True)


def test_selection_accounts_for_holes_invalid_predictions_and_rejected_good_depth():
    row = selection_stats([1., 2., np.nan, 1., 1.], [1., 1., 1., np.nan, 1.],
                          np.array([True, False, True, True, False]))
    assert row['coverage'] == 3/5 and row['sensor_coverage'] == 2/4
    assert row['good_retention'] == row['bad_retention'] == .5
    assert row['retained']['invalid_predictions'] == 1
    assert row['retained']['missing_reference'] == 1
    assert row['retained']['within_10_percent'] == .5
    assert row['rejected']['reference_samples'] == 2


def test_empty_selection_is_unscorable_not_perfect():
    row = selection_stats([1], [1], np.array([False]))
    assert row['retained']['valid_prediction_metrics'] is None
    assert not row['retained']['passed']
    assert row['sensor_coverage'] == 0
    assert selection_stats([1], [np.nan], np.array([True]))['sensor_coverage'] is None


def bundle(tmp_path):
    shape = (2, 2)
    cp, sp = tmp_path/'cross.npz', tmp_path/'sensor.npz'
    np.savez(cp, support=np.array([[2, 3], [0, 1]]), conflict=np.array([[0, 1], [0, 0]]),
             valid=np.ones(shape, dtype=bool))
    np.savez(sp, check=np.ones(shape, dtype=bool), prior_m=np.array([[1., 1.5], [1.2, 1.]]),
             sensor_m=np.ones(shape))
    args = SimpleNamespace(consistency=tmp_path/'cross.json', sensor=tmp_path/'sensor.json', output=tmp_path/'out')
    save(args.consistency, {'status':'complete','analysis_sha256':'shared',
         'config':{'grid_step':4,'min_other_views':2},'scenes':[{'excluded_views':[], 'views':[
         {'name':'a','grid_shape':shape,'map':cp.name,'map_sha256':digest(cp)}]}]})
    save(args.sensor, {'status':'complete','analysis_sha256':'shared','config':{'grid_step':4},
         'scale_m_per_sfm_unit':1,'views':[{'name':'a','sparse_screen_pass':True,
         'grid_shape':shape,'map':sp.name,'map_sha256':digest(sp)}]})
    return args


def test_frozen_masks_are_unchanged_when_sensor_values_change(tmp_path):
    args = bundle(tmp_path)
    first = evaluate(args)
    sp = tmp_path/'sensor.npz'
    with np.load(sp) as data:
        arrays = dict(data)
    arrays['sensor_m'] *= 2
    np.savez(sp, **arrays)
    manifest = json.loads(args.sensor.read_text())
    manifest['views'][0]['map_sha256'] = digest(sp)
    save(args.sensor, manifest)
    args.output = tmp_path/'other'
    second = evaluate(args)
    for key, expected in [('all',4),('supported',2),('supported_no_front_conflict',1)]:
        assert first['pooled'][key]['selected_positions'] == second['pooled'][key]['selected_positions'] == expected
    assert first['pooled']['supported']['retained'] != second['pooled']['supported']['retained']


@pytest.mark.parametrize('damage', ['scene', 'grid', 'set', 'digest', 'duplicate'])
def test_mixed_or_modified_diagnostics_are_rejected(tmp_path, damage):
    args = bundle(tmp_path)
    data = json.loads(args.sensor.read_text())
    if damage == 'scene': data['analysis_sha256'] = 'other'
    if damage == 'grid': data['config']['grid_step'] = 8
    if damage == 'set': data['views'][0]['name'] = 'other'
    if damage == 'digest': data['views'][0]['map_sha256'] = 'modified'
    if damage == 'duplicate': data['views'] *= 2
    save(args.sensor, data)
    with pytest.raises(ValueError): evaluate(args)


def test_duplicate_names_and_shape_broadcasting_are_rejected():
    with pytest.raises(ValueError): unique_rows([{'name':'a'}, {'name':'a'}])
    with pytest.raises(ValueError): selection_stats([1,1], [1], np.array([True, True]))
