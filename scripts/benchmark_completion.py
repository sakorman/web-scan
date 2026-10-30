"""Measure adapter exit-detection delay with controlled children, without a GPU.

The child writes a monotonic timestamp immediately before os._exit. The reported
delay includes writing that marker and exiting; it is not exact kernel exit time.
No model is produced. Real Brush reconstruction must be verified separately.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from benchmark_exports import freeze_adapters, load_adapter, run_order, sha


def run_control(adapter_path, work, delay):
    work.mkdir()
    marker = work / "exit-time.txt"
    code = (
        "import os,time,pathlib;"
        f"time.sleep({delay!r});"
        f"pathlib.Path({str(marker)!r}).write_text(str(time.monotonic()));"
        "os._exit(0)"
    )
    adapter = load_adapter(adapter_path)
    adapter.executable = lambda: Path(sys.executable)
    adapter.engine_identity = lambda _: {}
    children = []

    def launch(*args, **kwargs):
        child = subprocess.Popen([sys.executable, "-c", code], **kwargs)
        children.append(child)
        return child

    # Replace only this adapter's subprocess binding; preserve the real API.
    adapter.subprocess = SimpleNamespace(
        Popen=launch, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    started = time.monotonic()
    try:
        adapter.train(
            {}, work, work / "artifacts",
            {"steps": 500, "max_points": 150000, "resolution": 512},
            lambda *args: None,
        )
    except RuntimeError as error:
        detected = time.monotonic()
        if str(error) != "训练器未输出最终模型":
            raise
    else:
        raise RuntimeError("Control unexpectedly produced a model")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
    return {
        "child_delay_seconds": delay,
        "adapter_seconds": detected - started,
        "exit_marker_to_detection_seconds": detected - float(marker.read_text()),
        "adapter_sha256": sha(adapter_path),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5:
        parser.error("--repeats must be between 1 and 5")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    adapters = freeze_adapters(
        output, {"baseline": args.baseline, "candidate": args.candidate}
    )
    result = {
        "measurement": "controlled child exit marker to detection; no GPU or model",
        "repeats": args.repeats, "runs": [],
    }
    for index, delay in enumerate((0.1, 0.4, 0.7)):
        for repeat in range(args.repeats):
            for variant in run_order(index, repeat):
                row = run_control(
                    adapters[variant], output / f"{index}-{repeat}-{variant}", delay
                )
                result["runs"].append({**row, "variant": variant, "repeat": repeat})
                (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
