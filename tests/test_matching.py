import sys
from types import SimpleNamespace

import pytest

from webscan.reconstruction import match_images


class Options:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def fake_pycolmap(calls):
    return SimpleNamespace(
        Device=SimpleNamespace(cpu="cpu"),
        SequentialPairingOptions=Options,
        match_exhaustive=lambda **kwargs: calls.append(("exhaustive", kwargs)),
        match_sequential=lambda **kwargs: calls.append(("sequential", kwargs)),
    )


def test_exhaustive_matching_keeps_project_verification_settings(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pycolmap", fake_pycolmap(calls))
    assert match_images("database.db") == {"mode": "exhaustive", "ransac_seed": 42}
    name, args = calls.pop()
    assert name == "exhaustive"
    assert args["matching_options"] == {"num_threads": 4, "guided_matching": True}
    assert args["verification_options"] == {"ransac": {"random_seed": 42}}


def test_sequential_matching_uses_bounded_ordered_pairing(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pycolmap", fake_pycolmap(calls))
    result = match_images("database.db", mode="sequential", sequential_overlap=7)
    name, args = calls.pop()
    assert name == "sequential"
    assert args["pairing_options"].kwargs == {
        "overlap": 7,
        "quadratic_overlap": True,
        "loop_detection": False,
        "num_threads": 4,
    }
    assert result == {
        "mode": "sequential",
        "overlap": 7,
        "quadratic_overlap": True,
        "loop_detection": False,
        "ransac_seed": 42,
    }


@pytest.mark.parametrize("mode, overlap", [("other", 10), ("sequential", 0), ("sequential", 51)])
def test_matching_mode_rejects_unsupported_or_unbounded_values_before_native_import(
    monkeypatch, mode, overlap
):
    monkeypatch.setitem(sys.modules, "pycolmap", None)
    with pytest.raises(ValueError):
        match_images("database.db", mode=mode, sequential_overlap=overlap)
