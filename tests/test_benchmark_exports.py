import json

import pytest

from scripts.benchmark_exports import (
    freeze_adapters,
    run_order,
    summarize,
    write_comparison_artifacts,
)


def records():
    return [
        {
            "scene_index": 0,
            "repeat": repeat,
            "variant": mode,
            "input_sha256": "same",
            "training_views": 12,
            "evaluation_views": 2,
            "seconds": seconds,
            "gaussians": 1000,
            "evaluation": {"psnr": 20 + repeat, "ssim": 0.8},
        }
        for repeat, mode, seconds in [
            (0, "baseline", 10),
            (0, "candidate", 12),
            (1, "candidate", 14),
            (1, "baseline", 11),
        ]
    ]


def test_repeated_runs_counterbalance_order_and_report_range():
    assert run_order(0, 0) + run_order(0, 1) == [
        "baseline",
        "candidate",
        "candidate",
        "baseline",
    ]
    assert run_order(1, 0) + run_order(1, 1) == [
        "candidate",
        "baseline",
        "baseline",
        "candidate",
    ]
    summary = summarize(records(), 2)[0]
    assert summary["baseline"]["seconds"] == {"mean": 10.5, "min": 10, "max": 11}
    assert summary["candidate"]["seconds"] == {"mean": 13, "min": 12, "max": 14}
    assert summary["runs_per_variant"] == 2


@pytest.mark.parametrize("damage", ["missing", "duplicate", "input", "views", "metric"])
def test_invalid_comparisons_cannot_produce_summary(damage):
    data = records()
    if damage == "missing":
        data.pop()
    elif damage == "duplicate":
        data[-1] = data[0].copy()
    elif damage == "input":
        data[-1]["input_sha256"] = "different"
    elif damage == "metric":
        data[-1]["evaluation"]["psnr"] = float("nan")
    else:
        data[-1]["evaluation_views"] = 1
    with pytest.raises(ValueError):
        summarize(data, 2)


def test_adapter_edits_do_not_change_an_experiment_in_progress(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("setting = 100\n")
    output = tmp_path / "run"
    output.mkdir()
    frozen = freeze_adapters(output, {"baseline": source, "candidate": source})
    source.write_text("setting = 50\n")
    assert all(path.read_text() == "setting = 100\n" for path in frozen.values())
    assert frozen["baseline"] != frozen["candidate"]


def test_comparison_artifacts_match_task_layout(tmp_path):
    scene = {
        "views": [
            {
                "image": "/prepared/images/0000.jpg",
                "image_name": "0000.jpg",
                "view": [
                    [1, 0, 0, 2],
                    [0, 1, 0, 3],
                    [0, 0, 1, 4],
                    [0, 0, 0, 1],
                ],
                "K": [[100, 0, 50], [0, 100, 25], [0, 0, 1]],
            }
        ]
    }
    report = {"steps": 500, "evaluation": {"psnr": 20.0, "ssim": 0.8}}

    write_comparison_artifacts(tmp_path, scene, report)

    assert json.loads((tmp_path / "sfm/scene.json").read_text()) == scene
    cameras = json.loads((tmp_path / "artifacts/cameras.json").read_text())
    assert cameras["position"] == [-2.0, -3.0, -4.0]
    assert cameras["views"] == [
        {key: value for key, value in scene["views"][0].items() if key != "image"}
    ]
    assert json.loads((tmp_path / "artifacts/report.json").read_text()) == report
