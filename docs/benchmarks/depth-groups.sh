#!/bin/sh
set -eu
export PYTHONPATH=.
py=.venv/bin/python
r=${1:?new output directory required}
mkdir "$r"
cp docs/benchmarks/depth-groups-config.json "$r/config.json"
e=.data/benchmarks/depth-consistency-20261101
s=.data/benchmarks/depth-overlap-20261103
for job in pipes-baseline office-baseline; do
 "$py" scripts/benchmark_depth_overlap.py --job "$e/jobs/$job" --anchors "$e/anchors/anchors.json" \
  --analysis "$e/analysis.json" --predictions "$e/predictions" --consistency "$e/cross-view-v2/consistency.json" \
  --samples "$r/$job-samples" --output "$r/$job-overlap.json"
 "$py" scripts/benchmark_depth_groups.py --samples "$r/$job-samples" --output "$r/$job"
done
"$py" scripts/benchmark_depth_overlap.py --job "$s/jobs/tum-baseline" --anchors "$s/sensor/anchors.json" \
 --analysis "$s/analysis.json" --predictions "$s/predictions" --consistency "$s/masks/consistency.json" \
 --sensor "$s/evaluation/sensor-evaluation.json" --samples "$r/tum-samples" --output "$r/tum-overlap.json"
"$py" scripts/benchmark_depth_groups.py --samples "$r/tum-samples" --output "$r/tum"
