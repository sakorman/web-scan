import json
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from scripts.compare_quality import compare
from webscan.evaluation import validate_evaluation


def make_jobs(root, quick_size, detail_size, reference_size=(96, 64), legacy=False):
    for jid, dimensions, color in [('a', quick_size, 'black'), ('b', detail_size, 'white')]:
        work = root / 'jobs' / jid
        (work / 'artifacts').mkdir(parents=True)
        images = work / 'sfm/undistorted/images'
        images.mkdir(parents=True)
        views = []
        for i in range(14):
            path = images / f'{i:04d}.jpg'
            Image.new('RGB', reference_size, (i * 12, 40, 80)).save(path)
            views.append({'image': str(path)})
        scene = {'views': views}
        (work / 'sfm/scene.json').write_text(json.dumps(scene))
        (work / 'artifacts/cameras.json').write_text('{"views":[{"camera_id":1}]}')
        folder = work / 'brush/eval_500'
        folder.mkdir(parents=True)
        for i in [0, 8]:
            Image.new('RGB', dimensions, color).save(folder / f'{i:04d}.png')
        log = ('[time INFO brush_cli] Loaded dataset with 12 training, 2 eval views\n'
               '[time INFO brush_cli] Eval iter 500: PSNR 20, ssim 0.8\n')
        (work / 'brush.log').write_text(log)
        config = {'steps': 500, 'resolution': max(dimensions), 'evaluation_mode': 'holdout'}
        checked, _ = validate_evaluation(scene, work / 'brush', config, log)
        report = {**config, **checked}
        if legacy:
            del report['resolution']
        (work / 'artifacts/report.json').write_text(json.dumps(report))


@pytest.mark.parametrize('quick,detail,reference,legacy', [
    ((48, 32), (96, 64), (96, 64), False),
    ((32, 48), (64, 96), (64, 96), False),
    ((48, 32), (96, 64), (96, 64), True),
])
def test_requested_size_is_capped_by_both_renderings(tmp_path, quick, detail, reference, legacy):
    make_jobs(tmp_path, quick, detail, reference, legacy)
    capped = compare(tmp_path, 'a', 'b', size=512)
    native = compare(tmp_path, 'a', 'b', size=48)
    assert capped['mean'] == native['mean']
    assert capped['comparison_size'] == 48
    assert capped['requested_comparison_size'] == 512
    for row in capped['views']:
        dims = row['scoring_dimensions']
        assert [dims['width'], dims['height']] == list(quick)
        assert dims['quick_native'] == list(quick)
        assert dims['detail_native'] == list(detail)


def test_rounding_never_enlarges_a_short_side(tmp_path, monkeypatch):
    make_jobs(tmp_path, (48, 31), (96, 64))
    scorer = tmp_path / 'scorer'
    scorer.write_bytes(b'scorer')
    sizes = []

    def run(args, **kwargs):
        if args[-1] == '--version':
            return subprocess.CompletedProcess(args, 0, json.dumps({
                'protocol': 1, 'metric': 'lpips_vgg_brush', 'weights_sha256': 'a' * 64,
            }))
        folder = Path(args[-1])
        output = []
        for line in (folder / 'pairs.tsv').read_text().splitlines():
            index, render, reference = line.split('\t')
            with Image.open(folder / render) as a, Image.open(folder / reference) as b:
                assert a.size == b.size
                assert a.width <= 48 and a.height <= 31
                sizes.append(a.size)
            output.append(f'{index}\t0.2')
        return subprocess.CompletedProcess(args, 0, '\n'.join(output))

    monkeypatch.setattr('scripts.compare_quality.subprocess.run', run)
    result = compare(tmp_path, 'a', 'b', size=512, lpips_scorer=scorer)
    assert sizes and result['comparison_size'] == 48
    for row in result['views']:
        assert row['lpips_inputs']['width'] == row['scoring_dimensions']['width']
        assert row['lpips_inputs']['height'] == row['scoring_dimensions']['height']


def test_explicit_downsampling_keeps_its_requested_grid(tmp_path):
    make_jobs(tmp_path, (48, 32), (96, 64))
    result = compare(tmp_path, 'a', 'b', size=32)
    assert result['comparison_size'] == 32
    assert result['requested_comparison_size'] == 32
    assert all((x['scoring_dimensions']['width'], x['scoring_dimensions']['height']) == (32, 21)
               for x in result['views'])


def test_one_smaller_view_limits_the_whole_comparison(tmp_path):
    make_jobs(tmp_path, (96, 64), (96, 64))
    for jid in ['a', 'b']:
        work = tmp_path / 'jobs' / jid
        Image.new('RGB', (48, 32), 'red').save(work / 'sfm/undistorted/images/0008.jpg')
        Image.new('RGB', (48, 32), 'black').save(work / 'brush/eval_500/0008.png')
        scene = json.loads((work / 'sfm/scene.json').read_text())
        config = {'steps': 500, 'resolution': 96, 'evaluation_mode': 'holdout'}
        checked, _ = validate_evaluation(scene, work / 'brush', config, (work / 'brush.log').read_text())
        (work / 'artifacts/report.json').write_text(json.dumps({**config, **checked}))
    result = compare(tmp_path, 'a', 'b', 96)
    assert result['comparison_size'] == 48
    assert all((v['scoring_dimensions']['width'], v['scoring_dimensions']['height']) == (48, 32)
               for v in result['views'])


@pytest.mark.parametrize('scorer', [None, 'unused-scorer'])
def test_too_small_native_short_edge_fails_clearly(tmp_path, scorer):
    make_jobs(tmp_path, (16, 10), (96, 64))
    with pytest.raises(ValueError, match='SSIM needs image dimensions'):
        compare(tmp_path, 'a', 'b', size=512, lpips_scorer=scorer)


def test_legacy_report_with_a_smaller_first_photo(tmp_path):
    make_jobs(tmp_path, (96, 64), (96, 64), legacy=True)
    for jid in ['a', 'b']:
        work = tmp_path / 'jobs' / jid
        Image.new('RGB', (48, 32), 'red').save(work / 'sfm/undistorted/images/0000.jpg')
        Image.new('RGB', (48, 32), 'black').save(work / 'brush/eval_500/0000.png')
        scene = json.loads((work / 'sfm/scene.json').read_text())
        config = {'steps': 500, 'resolution': 96, 'evaluation_mode': 'holdout'}
        checked, _ = validate_evaluation(scene, work / 'brush', config, (work / 'brush.log').read_text())
        report = {**config, **checked}
        del report['resolution']
        (work / 'artifacts/report.json').write_text(json.dumps(report))
    result = compare(tmp_path, 'a', 'b', 512)
    assert result['comparison_size'] == 48
    assert result['views'][1]['scoring_dimensions']['quick_native'] == [96, 64]
