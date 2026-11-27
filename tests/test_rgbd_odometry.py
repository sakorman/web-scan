import json
import subprocess
import sys
import textwrap

import numpy as np
import pytest

from scripts.benchmark_rgbd_training import read_poses


def test_metric_scale_is_preserved_in_rgbd_scoring():
    # Torch and COLMAP have separate native OpenMP runtimes on macOS.
    program = """
    import numpy as np
    from scripts.benchmark_rgbd_odometry import trajectory_score
    centers = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    truth = np.column_stack((np.arange(4), centers, np.tile([0, 0, 0, 1], (4, 1))))
    poses = {i: np.eye(4) for i in range(4)}
    for i, matrix in poses.items():
        matrix[:3, 3] = 2 * centers[i] + 3
    frames = [(i, None) for i in range(4)]
    assert trajectory_score(poses, truth, frames, False)["center_rmse_m"] > .5
    assert trajectory_score(poses, truth, frames, True)["center_rmse_m"] < 1e-10
    """
    subprocess.run([sys.executable, "-c", textwrap.dedent(program)], check=True)



def test_pose_identity_and_orientation(tmp_path):
    frame = [{"timestamp": 1.0, "image": "frame.jpg", "sha256": "test"}]
    p = tmp_path / "poses.json"
    matrix = np.eye(4)
    def write():
        p.write_text(json.dumps({"frames": frame, "world_from_camera": {"0": matrix.tolist()}}))
    write()
    np.testing.assert_array_equal(read_poses(p, frame)[0], matrix)
    with pytest.raises(ValueError, match="RGB inputs"):
        read_poses(p, [])
    matrix[0, 0] = -1
    write()
    with pytest.raises(ValueError, match="Invalid metric pose"):
        read_poses(p, frame)


def test_pose_graph_preserves_rotated_reference_and_skipped_frame_ids():
    from scripts.rgbd_pose_graph import align_graph, propose_pairs

    poses = {}
    for i, theta in zip((3, 1, 9), (.4, -.1, .3)):
        matrix = np.eye(4)
        co, si = np.cos(theta), np.sin(theta)
        matrix[:2, :2] = [[co, -si], [si, co]]
        matrix[:3, 3] = [i / 100, 2, 1]
        poses[i] = matrix
    edges = [(i, j, np.linalg.inv(poses[j]) @ poses[i], 80)
             for i, j in ((3, 1), (3, 9), (1, 9))]
    result = align_graph(poses, edges)
    for i in poses:
        np.testing.assert_allclose(result[i], poses[i], atol=1e-10)
    assert propose_pairs(poses, {1}) == [(1, 9)]
