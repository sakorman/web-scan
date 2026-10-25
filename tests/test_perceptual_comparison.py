import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from scripts.compare_quality import _lpips_scores, compare
from webscan.evaluation import validate_evaluation


IDENTITY = {
    "protocol": 1, "metric": "lpips_vgg_brush", "version": "1", "weights_sha256": "a" * 64
}


def jobs(tmp_path):
    for name, color in [("a", "black"), ("b", "white")]:
        work = tmp_path / "jobs" / name
        (work / "artifacts").mkdir(parents=True)
        images = work / "sfm/undistorted/images"
        images.mkdir(parents=True)
        views = []
        for i in range(14):
            path = images / f"{i:04d}.jpg"
            Image.new("RGB", (96, 64), (i * 12, 40, 80)).save(path)
            views.append({"image": str(path)})
        scene = {"views": views}
        (work / "sfm/scene.json").write_text(json.dumps(scene))
        (work / "artifacts/cameras.json").write_text('{"views":[{"fx":80}]}')
        folder = work / "brush/eval_500"
        folder.mkdir(parents=True)
        for i in [0, 8]:
            Image.new("RGB", (48, 32), color).save(folder / f"{i:04d}.png")
        log = (
            "[time INFO brush_cli] Loaded dataset with 12 training, 2 eval views\n"
            "[time INFO brush_cli] Eval iter 500: PSNR 20.25, ssim 0.8\n"
        )
        (work / "brush.log").write_text(log)
        config = {"steps": 500, "resolution": 48, "evaluation_mode": "holdout"}
        checked, _ = validate_evaluation(scene, work / "brush", config, log)
        (work / "artifacts/report.json").write_text(json.dumps({**config, **checked}))
    scorer = tmp_path / "scorer"
    scorer.write_bytes(b"test scorer identity")
    return scorer


def test_optional_scores_use_identical_rgb_images_and_keep_existing_metrics(tmp_path, monkeypatch):
    scorer = jobs(tmp_path)
    ordinary = compare(tmp_path, "a", "b", 32)
    saved_hashes = []
    folders = []

    def run(args, **kwargs):
        if args[-1] == "--version":
            return subprocess.CompletedProcess(args, 0, json.dumps(IDENTITY))
        folder = Path(args[-1])
        folders.append(folder)
        rows = (folder / "pairs.tsv").read_text().splitlines()
        assert len(rows) == 4
        scores = []
        for line in rows:
            index, render, reference = line.split("\t")
            with Image.open(folder / render) as image:
                assert image.mode == "RGB" and image.size == (32, 21)
                expected = (0, 0, 0) if int(index) % 2 == 0 else (255, 255, 255)
                assert image.getpixel((0, 0)) == expected
            with Image.open(folder / reference) as image:
                assert image.mode == "RGB" and image.size == (32, 21)
            saved_hashes.append(hashlib.sha256((folder / render).read_bytes()).hexdigest())
            scores.append(f"{index}\t{(int(index) + 1) / 10}")
        # Protocol row order must not silently swap views or variants.
        return subprocess.CompletedProcess(args, 0, "\n".join(reversed(scores)))

    monkeypatch.setattr("scripts.compare_quality.subprocess.run", run)
    result = compare(tmp_path, "a", "b", 32, scorer)
    for i, row in enumerate(result["views"]):
        for offset, mode in enumerate(["quick", "detail"]):
            assert {k: row[mode][k] for k in ["psnr", "ssim"]} == ordinary["views"][i][mode]
            assert row[mode]["lpips"] == pytest.approx((i * 2 + offset + 1) / 10)
            assert row["lpips_inputs"][mode]["render_sha256"] == saved_hashes[i * 2 + offset]
    assert result["mean"]["quick"]["lpips"] == pytest.approx(0.2)
    assert result["mean"]["detail"]["lpips"] == pytest.approx(0.3)
    assert result["lpips_scorer"]["binary_sha256"] == hashlib.sha256(
        scorer.read_bytes()
    ).hexdigest()
    assert all(not folder.exists() for folder in folders)
    assert "lpips_scorer" not in ordinary


@pytest.mark.parametrize("output", [
    "0\t0.2", "0\t0.2\n0\t0.3", "0\tnan\n1\t0.3", "0\t-0.1\n1\t0.3",
    "0\t0.1\n1\t0.2\n2\t0.4", "broken row",
])
def test_incomplete_duplicate_invalid_scores_are_rejected(tmp_path, monkeypatch, output):
    scorer = tmp_path / "scorer"
    scorer.write_bytes(b"test")
    def run(args, **kwargs):
        text = json.dumps(IDENTITY) if args[-1] == "--version" else output
        return subprocess.CompletedProcess(args, 0, text)
    monkeypatch.setattr("scripts.compare_quality.subprocess.run", run)
    with pytest.raises(ValueError):
        _lpips_scores(scorer, tmp_path, 2)


@pytest.mark.parametrize("damage", ["camera", "reference", "all-photos", "missing-render"])
def test_invalid_evidence_is_rejected_before_scorer_execution(tmp_path, monkeypatch, damage):
    scorer = jobs(tmp_path)
    work = tmp_path / "jobs/b"
    if damage == "camera":
        (work / "artifacts/cameras.json").write_text('{"views":[{"fx":81}]}')
    elif damage == "reference":
        Image.new("RGB", (96, 64), "blue").save(work / "sfm/undistorted/images/0000.jpg")
    elif damage == "all-photos":
        path = work / "artifacts/report.json"
        data = json.loads(path.read_text())
        data["evaluation_mode"] = "all"
        path.write_text(json.dumps(data))
    else:
        (work / "brush/eval_500/0008.png").unlink()
    def run(*args, **kwargs):
        pytest.fail("Scorer must not run on invalid evidence")
    monkeypatch.setattr("scripts.compare_quality.subprocess.run", run)
    with pytest.raises(ValueError):
        compare(tmp_path, "a", "b", 32, scorer)


def test_unknown_metric_and_failed_process_are_rejected(tmp_path, monkeypatch):
    scorer = tmp_path / "scorer"
    scorer.write_bytes(b"test")
    def unknown(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, '{"protocol":1,"metric":"mse"}')
    monkeypatch.setattr("scripts.compare_quality.subprocess.run", unknown)
    with pytest.raises(ValueError, match="Unsupported"):
        _lpips_scores(scorer, tmp_path, 2)
    def failure(args, **kwargs):
        if args[-1] == "--version":
            return subprocess.CompletedProcess(args, 0, json.dumps(IDENTITY))
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr("scripts.compare_quality.subprocess.run", failure)
    with pytest.raises(subprocess.CalledProcessError):
        _lpips_scores(scorer, tmp_path, 2)


def test_scorer_requires_weight_identity(tmp_path, monkeypatch):
    scorer = tmp_path / "scorer"
    scorer.write_bytes(b"test")

    def run(args, **kwargs):
        assert args[-1] == "--version"
        return subprocess.CompletedProcess(
            args, 0, json.dumps({**IDENTITY, "weights_sha256": "unknown"})
        )

    monkeypatch.setattr("scripts.compare_quality.subprocess.run", run)
    with pytest.raises(ValueError, match="identify its compiled weights"):
        _lpips_scores(scorer, tmp_path, 2)
