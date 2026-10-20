import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from webscan.evaluation import validate_evaluation
from webscan.gaussian import export_gaussians


def fixture_scene(root, count=14):
    folder = root / "images"
    folder.mkdir(parents=True)
    paths = []
    for i in range(count):
        path = folder / f"{i:04d}.jpg"
        Image.new("RGB", (96, 64), (i * 12, 40, 80)).save(path)
        paths.append(path)
    return {
        "views": [{"image": str(path)} for path in paths],
        "normalization": {"center": [0, 0, 0], "scale": 1},
    }


def good_log(steps=1000, metric="PSNR 20.25, ssim 0.8"):
    return f"[time INFO brush_cli] Loaded dataset with 12 training, 2 eval views\n[time INFO brush_cli] Eval iter {steps}: {metric}\n"


def outputs(root):
    folder = root / "brush/eval_1000"
    folder.mkdir(parents=True)
    for name in ["0000.png", "0008.png"]:
        Image.new("RGB", (48, 32), "black").save(folder / name)
    return folder


CONFIG = {"steps": 1000, "resolution": 48, "evaluation_mode": "holdout"}


def test_complete_black_images_and_scientific_metrics_are_valid(tmp_path):
    scene = fixture_scene(tmp_path)
    outputs(tmp_path)
    report, images = validate_evaluation(
        scene, tmp_path / "brush", CONFIG, good_log(metric="PSNR 2.025e1, ssim -2e-2")
    )
    assert report["evaluation"] == {"psnr": 20.25, "ssim": -0.02}
    assert report["evaluation_verified"] and len(images) == 2
    assert [r["name"] for r in report["evaluation_images"]] == ["0000.png", "0008.png"]
    assert all(
        len(r["reference_sha256"]) == 64 and len(r["sha256"]) == 64
        for r in report["evaluation_images"]
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        good_log(500),
        good_log(metric="PSNR NaN, ssim 0.8"),
        good_log(metric="PSNR 20, ssim inf"),
        good_log(metric="PSNR 20, ssim 1.5"),
        good_log().replace("12 training", "11 training"),
    ],
)
def test_missing_stale_invalid_or_incomplete_metrics_are_rejected(tmp_path, text):
    scene = fixture_scene(tmp_path)
    outputs(tmp_path)
    with pytest.raises(ValueError):
        validate_evaluation(scene, tmp_path / "brush", CONFIG, text)


@pytest.mark.parametrize("damage", ["missing", "extra", "corrupt", "wrong-size"])
def test_expected_view_set_and_full_png_decode_are_checked(tmp_path, damage):
    scene = fixture_scene(tmp_path)
    folder = outputs(tmp_path)
    path = folder / "0008.png"
    if damage == "missing":
        path.unlink()
    elif damage == "extra":
        shutil.copy2(path, folder / "0009.png")
    elif damage == "corrupt":
        path.write_bytes(path.read_bytes()[:30])
    else:
        Image.new("RGB", (48, 48)).save(path)
    with pytest.raises(ValueError):
        validate_evaluation(scene, tmp_path / "brush", CONFIG, good_log())


def test_all_photos_mode_does_not_invent_evaluation(tmp_path):
    scene = fixture_scene(tmp_path)
    report, images = validate_evaluation(
        scene,
        tmp_path / "brush",
        {**CONFIG, "evaluation_mode": "all"},
        "[time INFO brush_cli] Loaded dataset with 14 training, 0 eval views\n",
    )
    assert (
        report["evaluation"] is None
        and not report["evaluation_verified"]
        and images == []
    )
    assert report["training_views"] == 14 and report["evaluation_views"] == 0


def test_failed_evaluation_preserves_raw_exports_and_publishes_no_model(
    tmp_path, monkeypatch
):
    from webscan import brush

    scene = fixture_scene(tmp_path)
    source = tmp_path / "seed"
    export_gaussians(
        source,
        np.array([[0, 0, 0]], dtype="f4"),
        np.array([[0.1, 0.2, 0.3]], dtype="f4"),
        np.array([[1, 0, 0, 0]], dtype="f4"),
        np.array([0.8]),
        np.array([[0.2, 0.3, 0.4]]),
    )

    class Finished:
        returncode = 0

        def poll(self):
            return 0

    def done(args, stdout, **kwargs):
        for name in ["export_0500.ply", "export_1000.ply"]:
            shutil.copy2(source / "gaussians.ply", tmp_path / "brush" / name)
        stdout.write(
            b"[time INFO brush_cli] Loaded dataset with 12 training, 2 eval views\n[time WARN brush_cli] Failed evaluation at iteration 1000\n"
        )
        stdout.flush()
        return Finished()

    monkeypatch.setattr(brush, "executable", lambda: Path("/fake"))
    monkeypatch.setattr(brush, "engine_identity", lambda _: {})
    monkeypatch.setattr(brush.subprocess, "Popen", done)
    with pytest.raises(ValueError, match="评测未完成"):
        brush.train(
            scene,
            tmp_path,
            tmp_path / "artifacts",
            {**CONFIG, "max_points": 50000},
            lambda *args: None,
        )
    assert not (tmp_path / "artifacts/gaussians.ply").exists()
    assert (tmp_path / "brush/export_0500.ply").exists() and (
        tmp_path / "brush/export_1000.ply"
    ).exists()


def test_comparison_cannot_accept_matching_but_partial_view_sets(tmp_path):
    from scripts.compare_quality import compare

    scene = fixture_scene(tmp_path)
    for jid in ["a", "b"]:
        root = tmp_path / "jobs" / jid
        (root / "artifacts").mkdir(parents=True)
        (root / "sfm").mkdir()
        (root / "artifacts/cameras.json").write_text('{"views":[]}')
        (root / "artifacts/report.json").write_text(
            json.dumps({**CONFIG, "evaluation_views": 2})
        )
        (root / "sfm/scene.json").write_text(json.dumps(scene))
        (root / "brush.log").write_text(good_log())
        folder = outputs(root)
        (folder / "0008.png").unlink()
    with pytest.raises(ValueError, match="评测图片不完整"):
        compare(tmp_path, "a", "b")


def test_comparison_checks_saved_image_digests(tmp_path):
    from scripts.compare_quality import compare

    for jid in ["a", "b"]:
        root = tmp_path / "jobs" / jid
        scene = fixture_scene(root / "sfm/undistorted")
        (root / "artifacts").mkdir()
        folder = outputs(root)
        (root / "sfm/scene.json").write_text(json.dumps(scene))
        (root / "artifacts/cameras.json").write_text('{"views":[]}')
        (root / "brush.log").write_text(good_log())
        checked, _ = validate_evaluation(scene, root / "brush", CONFIG, good_log())
        (root / "artifacts/report.json").write_text(json.dumps({**CONFIG, **checked}))
    Image.new("RGB", (48, 32), "white").save(folder / "0000.png")
    with pytest.raises(ValueError, match="changed since training"):
        compare(tmp_path, "a", "b")
