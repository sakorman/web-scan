import numpy as np
import pytest

from scripts.benchmark_sensor_depth import associate, fit_scene_scale, score_depth, sensor_samples


def test_timestamp_association_is_unique_bounded_and_closest():
    # Closest match claims depth 1.01; RGB 1.0 then has no match within 20ms.
    assert associate([1., 1.015, 2.], [1.01, 1.03, 2.1], .02) == {1: (0, abs(1.015-1.01))}
    assert associate([1., 2.], [2.005, 1.005], .02).keys() == {0, 1}


@pytest.mark.parametrize('rgb,depth,tolerance', [([1, 1], [1], .02), ([np.nan], [1], .02), ([1], [1], -1)])
def test_invalid_timestamp_association_fails(rgb, depth, tolerance):
    with pytest.raises(ValueError):
        associate(rgb, depth, tolerance)


def test_sensor_depth_units_holes_and_pixel_centers_without_interpolation():
    depth = np.array([[5000, 0], [10000, 15000]], dtype=np.uint16)
    values, valid = sensor_samples(depth, [[.5,.5], [1.5,.5], [.5,1.5], [1.5,1.5], [-.1,.5], [2.,1.5]])
    assert valid.tolist() == [True, False, True, True, False, False]
    assert values[valid].tolist() == [1, 2, 3]
    assert np.isnan(values[~valid]).all()
    assert sensor_samples(depth, [[.99,.5]])[0][0] == 1


def record():
    y,x = np.mgrid[:16,:16]
    xy = np.column_stack([x.ravel()+.5,y.ravel()+.5])
    z = np.full(len(xy), 2.)
    return {'xy':xy, 'z':z, 'sensor':z*3, 'width':16, 'height':16}


def test_scene_gauge_uses_only_fit_regions_and_is_one_shared_scale():
    from scripts.benchmark_depth_prior import fit_cells
    first, second = record(), record()
    check = ~fit_cells(first['xy'],16,16)
    first['sensor'][check] = 1000
    scale, counts = fit_scene_scale([first, second])
    assert scale == 3 and counts == [128,128]
    second['sensor'] *= 2
    scale, counts = fit_scene_scale([first, second])
    assert scale == 4.5


def test_scene_gauge_excludes_missing_sensor_and_rejects_insufficient_support():
    rec = record(); rec['sensor'][:250] = np.nan
    with pytest.raises(ValueError, match='Insufficient'):
        fit_scene_scale([rec])


def test_invalid_predictions_remain_in_accuracy_denominator():
    result = score_depth([2.,np.nan,-1.,3.,8.], [2.,2.,2.,0.,np.nan])
    assert result['reference_samples'] == 3 and result['missing_reference'] == 2
    assert result['invalid_predictions'] == 2
    assert result['within_10_percent'] == result['delta_1_25'] == 1/3
    assert result['valid_prediction_metrics']['mean_abs_rel'] == 0
    assert not result['passed']


def test_depth_accuracy_and_no_reference_are_explicit():
    assert score_depth([1.,2.,3.], [1.,2.,3.])['passed']
    assert not score_depth([1.], [0.])['passed']
    assert score_depth([np.nan], [1.])['valid_prediction_metrics'] is None
    result = score_depth([2.,4.], [1.,2.])
    assert result['valid_prediction_metrics']['mean_abs_rel'] == 1
    assert result['delta_1_25'] == 0 and not result['passed']
    with pytest.raises(ValueError):
        score_depth([1], [1,2])


def test_sensor_evaluation_identity_and_known_metric_depth(tmp_path):
    from argparse import Namespace
    from PIL import Image
    from scripts.benchmark_depth_prior import digest, save
    from scripts.benchmark_sensor_depth import evaluate

    y,x = np.mgrid[:16,:16]
    z = 1 + (x+y)/20
    depth = np.rint(z*5000).astype(np.uint16)
    Image.fromarray(depth).save(tmp_path/'depth.png')
    Image.new('RGB',(16,16)).save(tmp_path/'rgb.png')
    np.save(tmp_path/'prediction.npy',1/z)
    xy = np.column_stack([x.ravel()+.5,y.ravel()+.5])
    view = {'name':'test.jpg','job':'test','image':str(tmp_path/'rgb.png'),
            'image_sha256':digest(tmp_path/'rgb.png'),'xy':xy.tolist(),
            'z':(z.ravel()/3).tolist(),'width':16,'height':16}
    save(tmp_path/'anchors.json',{'views':[view]})
    save(tmp_path/'config.json',{'gate':{'median_abs_rel':.1,'p90_abs_rel':.25},
                               'grid_step':2,'depth_factor':5000})
    save(tmp_path/'sensor.json',{'config_sha256':digest(tmp_path/'config.json'),
         'anchor_sha256':digest(tmp_path/'anchors.json'),
         'views':[{'name':'test.jpg','depth':'depth.png','depth_sha256':digest(tmp_path/'depth.png')}]})
    save(tmp_path/'analysis.json',{'anchor_sha256':digest(tmp_path/'anchors.json'),
         'views':[{'name':'test.jpg','scale':3.,'shift':0.,'passed':True,'reason':'screen_pass'}],
         'inference':{'status':'complete','anchor_sha256':digest(tmp_path/'anchors.json'),
                      'views':[{'name':'test.jpg','image_sha256':view['image_sha256'],
                                'prediction':'prediction.npy','prediction_sha256':digest(tmp_path/'prediction.npy')}]}})
    args=Namespace(config=tmp_path/'config.json',anchors=tmp_path/'anchors.json',
                   manifest=tmp_path/'sensor.json',analysis=tmp_path/'analysis.json',
                   predictions=tmp_path,output=tmp_path/'first')
    evaluate(args)
    import json
    result=json.loads((args.output/'sensor-evaluation.json').read_text())
    assert result['status']=='complete' and np.isclose(result['scale_m_per_sfm_unit'],3)
    row=result['views'][0]['prior']
    assert row['reference_samples']==32 and row['passed']
    assert row['valid_prediction_metrics']['mean_abs_rel']<1e-12
    # A changed sensor or predictor must never be silently scored as the frozen run.
    original=(tmp_path/'depth.png').read_bytes()
    (tmp_path/'depth.png').write_bytes(b'changed')
    args.output=tmp_path/'bad_sensor'
    with pytest.raises(AssertionError): evaluate(args)
    (tmp_path/'depth.png').write_bytes(original)
    np.save(tmp_path/'prediction.npy',np.ones((16,16)))
    args.output=tmp_path/'bad_prediction'
    with pytest.raises(AssertionError): evaluate(args)
    assert json.loads((args.output/'sensor-evaluation.json').read_text())['status']=='failed'
    save(tmp_path/'anchors.json', {'views':[view, {**view,'job':'another_scene','name':'other.jpg'}]})
    args.output=tmp_path/'bad_scene'
    with pytest.raises(ValueError, match='exactly one SfM'):
        evaluate(args)
