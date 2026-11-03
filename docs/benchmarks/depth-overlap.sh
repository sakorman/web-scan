#!/bin/sh
# Run from the repository root with the prepared benchmark scenes and optional MPS dependencies.
set -eu
r=${1:?provide a new output directory under .data/benchmarks}
e=.data/benchmarks/depth-consistency-20261101
s=.data/benchmarks/depth-sensor-20261101
u=.data/benchmarks/depth-support-20261102
export PYTHONPATH=.
py=.venv/bin/python
mkdir "$r"
mkdir "$r/verified"
cp docs/benchmarks/depth-overlap-config.json "$r/config.json"
"$py" docs/benchmarks/depth-sensor-run.py --root "$r" >"$r/reconstruction.log" 2>&1
"$py" scripts/benchmark_sensor_depth.py prepare --work "$r/jobs/tum-baseline" \
  --archive .samples/archives/tum-freiburg1-desk.tgz --config "$r/config.json" --output "$r/sensor"
PYTORCH_ENABLE_MPS_FALLBACK=0 PYTHONPATH=.:.tools/depth-prior-deps "$py" scripts/benchmark_depth_prior.py infer \
  --anchors "$r/sensor/anchors.json" --repository .tools/depth-anything-v2 \
  --weights .tools/depth-anything-v2/depth_anything_v2_vits.pth --device mps --input-size 518 --output "$r/predictions"
"$py" scripts/benchmark_depth_prior.py analyze --anchors "$r/sensor/anchors.json" --predictions "$r/predictions" --output "$r/analysis.json"
"$py" scripts/benchmark_depth_support.py masks --anchors "$r/sensor/anchors.json" --analysis "$r/analysis.json" \
  --predictions "$r/predictions" --config "$r/config.json" --output "$r/masks"
"$py" scripts/benchmark_sensor_depth.py evaluate --anchors "$r/sensor/anchors.json" --manifest "$r/sensor/sensor.json" \
  --predictions "$r/predictions" --analysis "$r/analysis.json" --config "$r/config.json" --output "$r/evaluation"
for job in pipes-baseline office-baseline; do
  "$py" scripts/benchmark_depth_overlap.py --job "$e/jobs/$job" --anchors "$e/anchors/anchors.json" \
    --analysis "$e/analysis.json" --predictions "$e/predictions" --consistency "$e/cross-view-v2/consistency.json" --output "$r/verified/$job.json"
done
"$py" scripts/benchmark_depth_overlap.py --job "$s/jobs/tum-baseline" --anchors "$s/sensor-v2/anchors.json" \
  --analysis "$s/analysis.json" --predictions "$s/predictions-v2" --consistency "$u/masks/consistency.json" \
  --sensor "$s/evaluation-v3/sensor-evaluation.json" --output "$r/verified/tum-frozen.json"
"$py" scripts/benchmark_depth_overlap.py --job "$r/jobs/tum-baseline" --anchors "$r/sensor/anchors.json" \
  --analysis "$r/analysis.json" --predictions "$r/predictions" --consistency "$r/masks/consistency.json" \
  --sensor "$r/evaluation/sensor-evaluation.json" --output "$r/verified/tum-new.json"
"$py" scripts/compare_quality.py --data "$r" --quick tum-baseline --detail tum-baseline --output "$r/rgb-scores.json"
PYTHONPATH=.:.tools/depth-plot-deps "$py" docs/benchmarks/depth-overlap-report.py --root "$r"
