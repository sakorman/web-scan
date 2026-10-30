"""Compare model conversion in fresh processes, without retraining or using GPU."""

import argparse
import hashlib
import importlib.util
import json
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def measure(adapter, job, output):
    spec = importlib.util.spec_from_file_location("conversion_adapter", adapter)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = json.loads((job / "artifacts/report.json").read_text())
    steps = report["steps"]
    source = job / "brush" / f"export_{steps:0{len(str(steps))}d}.ply"
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    count = module.convert_output(
        source, output, report["normalization"], expected_sh_degree=3
    )
    elapsed = time.perf_counter() - started
    # Capture the peak before digest checks. macOS reports bytes; Linux KiB.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = int(peak * (1 if sys.platform == "darwin" else 1024))
    return {
        "seconds": elapsed,
        "process_peak_rss_bytes": peak_bytes,
        "gaussians": count,
        "source_sha256": sha(source),
        "outputs": {
            name: {"bytes": (output / name).stat().st_size, "sha256": sha(output / name)}
            for name in ("gaussians.ply", "preview.splat")
        },
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--candidate", type=Path)
    p.add_argument("--jobs", nargs="+", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--worker-adapter", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--worker-job", type=Path, help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.worker_adapter:
        print(json.dumps(measure(args.worker_adapter, args.worker_job, args.output)))
        return
    if not args.baseline or not args.candidate or not args.jobs or not 1 <= args.repeats <= 10:
        p.error("Require --baseline, --candidate, --jobs and 1–10 repeats")
    if sys.platform not in ("darwin", "linux"):
        p.error("RSS measurement supports macOS and Linux")
    args.output.mkdir(parents=True, exist_ok=False)
    frozen = {}
    for label in ("baseline", "candidate"):
        frozen[label] = args.output / f"{label}.py"
        shutil.copy2(getattr(args, label), frozen[label])
    result = {
        "platform": platform.platform(),
        "measurement": "fresh process peak RSS; conversion wall time excludes imports and hashing; filesystem cache not flushed; no fsync or GPU",
        "adapters": {label: sha(path) for label, path in frozen.items()},
        "runs": [],
    }
    for index, job in enumerate(args.jobs):
        identities = set()
        for repeat in range(args.repeats):
            order = ["baseline", "candidate"]
            if (index + repeat) % 2:
                order.reverse()
            for label in order:
                output = args.output / f"{index}-{repeat}-{label}"
                child = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()),
                     "--worker-adapter", str(frozen[label].resolve()),
                     "--worker-job", str(job.resolve()), "--output", str(output.resolve())],
                    check=True, capture_output=True, text=True, timeout=120,
                )
                row = json.loads(child.stdout)
                row.update(job_id=job.name, variant=label, repeat=repeat)
                identities.add(json.dumps([row["gaussians"], row["source_sha256"], row["outputs"]], sort_keys=True))
                if len(identities) != 1:
                    raise ValueError("Conversion output bytes differ")
                result["runs"].append(row)
                print(json.dumps(row), flush=True)
                (args.output / "results.json").write_text(json.dumps(result, indent=2))
    result["summary"] = []
    for job in args.jobs:
        summary = {"job_id": job.name}
        for label in frozen:
            rows = [r for r in result["runs"] if r["job_id"] == job.name and r["variant"] == label]
            summary[label] = {
                key: {"median": statistics.median(r[key] for r in rows),
                      "min": min(r[key] for r in rows), "max": max(r[key] for r in rows)}
                for key in ("seconds", "process_peak_rss_bytes")
            }
        result["summary"].append(summary)
    (args.output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
