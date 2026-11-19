import json
import sqlite3
from contextlib import closing

import pytest

from scripts import compare_repeats as repeated


JOBS = ['a' * 32, 'b' * 32, 'c' * 32]


@pytest.fixture
def catalog(tmp_path):
    with closing(sqlite3.connect(tmp_path / 'state.sqlite3')) as db, db:
        db.execute('CREATE TABLE jobs(id TEXT, status TEXT, config TEXT, report TEXT)')
        for jid in JOBS:
            config = dict(backend='brush', steps=3000, max_points=150000,
                          resolution=512, camera_mode='shared', evaluation_mode='holdout')
            report = dict(backend='brush', steps=3000, resolution=512, evaluation_mode='holdout',
                          engine_sha256='f' * 64, training_seed=42, evaluation_verified=True,
                          training_views=12, evaluation_views=2, gaussians=100)
            work = tmp_path / 'jobs' / jid
            (work / 'artifacts').mkdir(parents=True)
            (work / 'sfm/undistorted/images').mkdir(parents=True)
            (work / 'sfm/undistorted/images/train.jpg').write_bytes(b'fixed photo')
            (work / 'sfm/scene.json').write_text(json.dumps({'views': [
                {'image': str(work / 'sfm/undistorted/images/train.jpg'), 'K': [[1, 0, 0]]}]}))
            (work / 'artifacts/cameras.json').write_text('{"views": []}')
            (work / 'artifacts/report.json').write_text(json.dumps(report))
            (work / 'artifacts/gaussians.ply').write_bytes(b'catalog metadata fixture')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)',
                       (jid, 'succeeded', json.dumps(config), json.dumps(report)))
    return tmp_path


def rewrite(data, *, config_change=None, report_change=None):
    with closing(sqlite3.connect(data / 'state.sqlite3')) as db, db:
        config, report = map(json.loads, db.execute(
            'SELECT config,report FROM jobs WHERE id=?', (JOBS[1],)).fetchone())
        config.update(config_change or {})
        report.update(report_change or {})
        db.execute('UPDATE jobs SET config=?,report=? WHERE id=?',
                   (json.dumps(config), json.dumps(report), JOBS[1]))
        (data / 'jobs' / JOBS[1] / 'artifacts/report.json').write_text(json.dumps(report))


def test_repeats_preserve_scores_and_report_sample_spread(catalog, monkeypatch):
    calls = []

    def score(data, first, jid, size):
        calls.append(jid)
        i = JOBS.index(jid)
        scores = dict(psnr=20.0 + i, ssim=0.8 + i * 0.01)
        return dict(mean={'detail': scores}, views=[dict(
            view='heldout.png', detail=scores, scoring_dimensions={'width': 512, 'height': 341})])

    monkeypatch.setattr(repeated, 'compare', score)
    result = repeated.summarize(catalog, JOBS)
    assert calls == JOBS
    assert result['score_summary']['psnr'] == dict(count=3, mean=21, sample_std=1, min=20, max=22, range=2)
    assert result['view_summary'][0]['psnr'] == result['score_summary']['psnr']
    assert [r['scores']['psnr'] for r in result['runs']] == [20, 21, 22]


@pytest.mark.parametrize('changes,pattern', [
    ({'config_change': {'max_points': 300000}}, 'config'),
    ({'report_change': {'engine_sha256': 'e' * 64}}, 'engine_sha256'),
    ({'report_change': {'training_seed': 7}}, 'training_seed'),
    ({'report_change': {'training_seed': None}}, 'recorded training seed'),
    ({'report_change': {'training_views': 11}}, 'training_views'),
    ({'report_change': {'steps': 6000}}, 'Report and config disagree'),
    ({'config_change': {'evaluation_mode': 'all'}}, 'held-out'),
    ({'report_change': {'evaluation_verified': False}}, 'Verified'),
])
def test_repeats_reject_mismatched_conditions_before_scoring(catalog, monkeypatch, changes, pattern):
    rewrite(catalog, **changes)
    monkeypatch.setattr(repeated, 'compare', lambda *a: pytest.fail('Must reject before scoring'))
    with pytest.raises(ValueError, match=pattern):
        repeated.summarize(catalog, JOBS)


def test_repeats_hash_training_photos_not_only_heldout(catalog):
    (catalog / 'jobs' / JOBS[1] / 'sfm/undistorted/images/train.jpg').write_bytes(b'changed photo')
    with pytest.raises(ValueError, match='inputs_sha256'):
        repeated.summarize(catalog, JOBS)


def test_repeats_reject_duplicate_ids(catalog):
    with pytest.raises(ValueError, match='distinct'):
        repeated.summarize(catalog, [JOBS[0], JOBS[0]])


def test_repeats_do_not_create_a_missing_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        repeated.summarize(tmp_path, JOBS)
    assert not (tmp_path / 'state.sqlite3').exists()


def test_repeats_reject_artifact_report_drift(catalog):
    (catalog / 'jobs' / JOBS[1] / 'artifacts/report.json').write_text('{}')
    with pytest.raises(ValueError, match='Catalog and artifact'):
        repeated.summarize(catalog, JOBS)


@pytest.mark.parametrize('relative,content,pattern', [
    ('artifacts/cameras.json', '{"views": [1]}', 'cameras_sha256'),
    ('sfm/scene.json', '{"views": [], "points": [[1,2,3]]}', 'scene_sha256'),
])
def test_repeats_reject_geometry_changes(catalog, relative, content, pattern):
    (catalog / 'jobs' / JOBS[1] / relative).write_text(content)
    with pytest.raises(ValueError, match=pattern):
        repeated.summarize(catalog, JOBS)


def test_repeats_reject_inconsistent_scoring_grids(catalog, monkeypatch):
    def score(data, first, jid, size):
        scores = dict(psnr=20.0, ssim=0.8)
        return dict(mean={'detail': scores}, views=[dict(view='heldout.png', detail=scores,
            scoring_dimensions={'width': 512 if jid == first else 511, 'height': 341})])
    monkeypatch.setattr(repeated, 'compare', score)
    with pytest.raises(ValueError, match='Scoring grids'):
        repeated.summarize(catalog, JOBS)
