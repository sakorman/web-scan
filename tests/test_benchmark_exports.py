import pytest

from scripts.benchmark_exports import freeze_adapters, run_order, summarize


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
