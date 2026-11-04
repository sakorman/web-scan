import numpy as np
import pytest

from scripts.benchmark_depth_groups import diverse_three, group_samples, summarize


def test_leader_radius_does_not_merge_transitive_chain():
    xyz = np.array([[0., 0., 10.], [.09, 0., 10.], [.18, 0., 10.]])
    ids, leaders = group_samples(xyz, np.full(3, 10.), .01)
    assert ids.tolist() == [0, 0, 1]
    assert leaders.tolist() == [0, 2]


def test_source_observations_do_not_inflate_independent_views():
    xyz = np.array([[0., 0., 10.], [.01, 0., 10.], [.02, 0., 10.]])
    z = np.full(3, 10.)
    ids, leaders = group_samples(xyz, z, .01)
    stats, rows = summarize(xyz, z, np.zeros(3, int), np.array([[0., 0., 0.]]), ids, leaders)
    assert stats['samples'] == 3 and stats['groups'] == 1
    assert stats['three_views'] == 0
    assert rows[0]['source_views'] == 1


def test_three_pairwise_separated_rays_required():
    point = np.array([0., 0., 10.])
    cameras = np.array([[-1., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    assert diverse_three(point, cameras)
    cameras[2] = cameras[0]  # Different filenames at the same center add no parallax.
    assert not diverse_three(point, cameras)


def test_similarity_transform_preserves_assignments():
    xyz = np.array([[0., 0., 10.], [.09, 0., 10.], [.18, 0., 10.]])
    z = np.full(3, 10.)
    rotate = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    a = group_samples(xyz, z, .01)
    b = group_samples(7*xyz @ rotate + [20, 30, 40], 7*z, .01)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))


def test_depth_radius_uses_both_source_and_leader_z():
    xyz = np.array([[0., 0., 0.], [.09, 0., 0.]])
    ids, _ = group_samples(xyz, np.array([100., 1.]), .01)
    assert ids.tolist() == [0, 1]


def test_sensor_is_audit_and_missing_or_near_sparse_measurement_cannot_confirm():
    xyz = np.array([[0., 0., 10.], [.01, 0., 10.], [.02, 0., 10.]])
    z = np.full(3, 10.)
    ids, leaders = group_samples(xyz, z, .01)
    source = np.arange(3)
    cameras = np.array([[-1., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    base, _ = summarize(xyz, z, source, cameras, ids, leaders)
    audit, _ = summarize(xyz, z, source, cameras, ids, leaders, xyz.copy(), z, np.ones(3))
    assert audit['diverse_sensor_confirmed'] == 1
    assert all(audit[k] == v for k, v in base.items())
    missing = xyz.copy(); missing[1] = np.nan
    audit, _ = summarize(xyz, z, source, cameras, ids, leaders, missing, z, np.ones(3))
    assert audit['diverse_with_three_sensor_views'] == audit['diverse_sensor_confirmed'] == 0
    audit, _ = summarize(xyz, z, source, cameras, ids, leaders, xyz, z, np.array([0., 1., 1.]))
    assert audit['diverse_with_three_sensor_views'] == 1 and audit['diverse_sensor_confirmed'] == 0


def test_empty_input_is_zero_groups_and_invalid_input_rejected():
    ids, leaders = group_samples(np.empty((0, 3)), np.empty(0), .01)
    result, _ = summarize(np.empty((0, 3)), np.empty(0), np.empty(0, int),
                          np.empty((0, 3)), ids, leaders)
    assert result['groups'] == result['diverse_three'] == 0
    with pytest.raises(ValueError):
        group_samples(np.array([[np.nan, 0, 0]]), np.array([1.]), .01)
    with pytest.raises(ValueError):
        group_samples(np.ones((1, 3)), np.array([0.]), .01)


def test_manifest_identity_and_source_deduplication(tmp_path):
    from types import SimpleNamespace
    from scripts.benchmark_depth_groups import run
    from scripts.benchmark_depth_prior import digest, save

    folder = tmp_path/'samples'
    folder.mkdir()
    data = dict(world=np.array([[0., 0., 10.]]), z=np.array([10.]), grid_index=np.array([0]),
                image_distance=np.array([.02]), space_distance=np.array([.02]), conflict=np.array([False]))
    rows = []
    for i, center in enumerate([[-1, 0, 0], [1, 0, 0], [0, 1, 0]]):
        path = folder/f'{i}.npz'
        np.savez(path, **data)
        rows.append(dict(name=str(i), image_sha256=str(i), map=path.name, map_sha256=digest(path), center=center))
    manifest = dict(views=rows, overlap=dict(pooled={'0.01': dict(both_far_no_front_conflict=3)}))
    save(folder/'samples.json', manifest)
    result = run(SimpleNamespace(samples=folder, output=tmp_path/'result'))
    assert all(v['summary']['diverse_three'] == 1 for v in result['variants'])
    (folder/'0.npz').write_bytes(b'changed')
    with pytest.raises(ValueError, match='identity'):
        run(SimpleNamespace(samples=folder, output=tmp_path/'changed'))
    rows[1]['image_sha256'] = rows[0]['image_sha256']
    save(folder/'samples.json', manifest)
    with pytest.raises(ValueError, match='Repeated'):
        run(SimpleNamespace(samples=folder, output=tmp_path/'duplicate'))
