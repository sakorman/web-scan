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
