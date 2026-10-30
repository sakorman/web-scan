"""Completion, progress and failure handling across timed process waits."""

from types import SimpleNamespace

import pytest

from webscan import brush


def process_fixture(tmp_path, monkeypatch, finish=0.2, exit_code=0):
    clock = SimpleNamespace(now=0.0)
    waits = []

    class Process:
        returncode = None
        terminated = False

        def poll(self):
            if clock.now >= finish and self.returncode is None:
                self.returncode = exit_code
            return self.returncode

        def wait(self, timeout):
            waits.append(timeout)
            if self.poll() is not None:
                return self.returncode
            clock.now = min(finish, clock.now + timeout)
            if self.poll() is None:
                raise brush.subprocess.TimeoutExpired("brush", timeout)
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

    process = Process()

    def launch(*args, stdout, **kwargs):
        stdout.write(b"controlled failure detail\n")
        stdout.flush()
        return process

    monkeypatch.setattr(brush, "executable", lambda: tmp_path / "brush-test")
    monkeypatch.setattr(brush, "engine_identity", lambda _: {})
    monkeypatch.setattr(brush.subprocess, "Popen", launch)
    monkeypatch.setattr(
        brush, "time", SimpleNamespace(
            monotonic=lambda: clock.now,
            sleep=lambda seconds: setattr(clock, "now", clock.now + seconds),
        )
    )

    def run(progress):
        return brush.train(
            {}, tmp_path, tmp_path / "artifacts",
            {"steps": 500, "max_points": 150000, "resolution": 512}, progress,
        )

    return run, process, clock, waits


@pytest.mark.parametrize("finish,exit_code", [(0.2, 0), (2.2, 0), (0.2, 3)])
def test_exit_continues_without_an_extra_progress_interval(
    tmp_path, monkeypatch, finish, exit_code
):
    run, process, clock, waits = process_fixture(
        tmp_path, monkeypatch, finish, exit_code
    )
    updates = []
    expected = "controlled failure detail" if exit_code else "训练器未输出最终模型"
    with pytest.raises(RuntimeError, match=expected):
        run(lambda *args: updates.append(clock.now))
    assert clock.now == finish
    assert updates == ([0.0, 1.0, 2.0] if finish > 2 else [0.0])
    assert not process.terminated
    assert process.returncode == exit_code
    assert not (tmp_path / "artifacts/gaussians.ply").exists()


def test_progress_exception_still_terminates_child(tmp_path, monkeypatch):
    run, process, _, waits = process_fixture(tmp_path, monkeypatch, finish=30)

    def cancelled(*args):
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        run(cancelled)
    assert process.terminated and waits == [10]


def test_overall_deadline_still_terminates_child(tmp_path, monkeypatch):
    run, process, clock, waits = process_fixture(tmp_path, monkeypatch, finish=4000)

    def deadline(*args):
        clock.now = 3601

    with pytest.raises(RuntimeError, match="超过一小时"):
        run(deadline)
    assert process.terminated and waits[-1] == 10
