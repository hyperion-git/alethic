#!/usr/bin/env bash
set -euo pipefail
cd ~/dev/alethic
MODEL="qwen/qwen3.6-plus-preview:free"
SLUG="qwen"
OUTDIR="data/calibration/$SLUG"
mkdir -p "$OUTDIR" "docs/results/$SLUG"
echo "=== Qwen 3.6: Calibrate → Simulate → Breadth vs Depth ==="

# Stage 1: Calibrate
/home/xeal/.local/bin/micromamba run -n alethic python scripts/e_vs_f_calibrate.py \
  -p thorough --openrouter -m "$MODEL" -w 1 -o "$OUTDIR"

# Stage 2: Simulate
/home/xeal/.local/bin/micromamba run -n alethic python scripts/e_vs_f_simulate.py \
  --sweep -n 5000 -t 2000 \
  -d "$OUTDIR/e-vs-f-distributions.json" \
  -o "docs/results/$SLUG/e-vs-f-report.md"

# Stage 3: Breadth vs Depth
/home/xeal/.local/bin/micromamba run -n alethic python scripts/breadth_vs_depth.py \
  -m "$MODEL" --depth-iters 12 --breadth-runs 6 -r 5 \
  -o "$OUTDIR/bvd.json"

echo "=== Qwen 3.6: DONE ==="
