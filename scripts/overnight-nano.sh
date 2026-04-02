#!/usr/bin/env bash
set -uo pipefail
cd ~/dev/alethic
MODEL="nvidia/nemotron-3-nano-30b-a3b:free"
SLUG="nano"
OUTDIR="data/calibration/$SLUG"
mkdir -p "$OUTDIR" "docs/results/$SLUG"
echo "=== Nemotron Nano: Calibrate → Simulate → Breadth vs Depth ==="

# Stage 1: Calibrate (quality gate failure is non-fatal — distributions still written)
/home/xeal/.local/bin/micromamba run -n alethic python scripts/e_vs_f_calibrate.py \
  -p thorough --openrouter -m "$MODEL" -w 1 -o "$OUTDIR" \
  && echo "Stage 1: PASSED" || echo "Stage 1: quality gate failed (continuing)"

# Stage 2: Simulate (skip if Stage 1 produced no distributions)
if [ -f "$OUTDIR/e-vs-f-distributions.json" ]; then
  /home/xeal/.local/bin/micromamba run -n alethic python scripts/e_vs_f_simulate.py \
    --sweep -n 5000 -t 2000 \
    -d "$OUTDIR/e-vs-f-distributions.json" \
    -o "docs/results/$SLUG/e-vs-f-report.md" \
    && echo "Stage 2: DONE" || echo "Stage 2: failed (continuing)"
else
  echo "Stage 2: SKIPPED (no distributions file)"
fi

# Stage 3: Breadth vs Depth (independent of Stages 1-2)
/home/xeal/.local/bin/micromamba run -n alethic python scripts/breadth_vs_depth.py \
  -m "$MODEL" --depth-iters 12 --breadth-runs 6 -r 5 \
  -o "$OUTDIR/bvd.json"

echo "=== Nemotron Nano: DONE ==="
