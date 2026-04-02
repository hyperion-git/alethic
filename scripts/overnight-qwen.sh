#!/usr/bin/env bash
set -uo pipefail
cd ~/dev/alethic
MODEL="qwen/qwen3.6-plus-preview:free"
SLUG="qwen"
OUTDIR="data/calibration/$SLUG"
STAGES=3
mkdir -p "$OUTDIR" "docs/results/$SLUG"

pipeline_start=$SECONDS
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Qwen 3.6 — Overnight Experiment Pipeline          ║"
echo "║  Stage 1: Calibrate (extreme) → 20 problems            ║"
echo "║  Stage 2: Simulate (5000 trials)                        ║"
echo "║  Stage 3: Breadth vs Depth (20 problems × 5 reps)      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Stage 1/3: Calibrate
stage_start=$SECONDS
echo "▶ [Stage 1/$STAGES] Calibrate — extreme preset, 20 problems"
echo "  Model: $MODEL"
echo "  Output: $OUTDIR"
echo ""
/home/xeal/.local/bin/micromamba run -n alethic python scripts/e_vs_f_calibrate.py \
  -p extreme --openrouter -m "$MODEL" -w 1 -o "$OUTDIR" \
  && echo "" && echo "✓ [Stage 1/$STAGES] PASSED ($(( SECONDS - stage_start ))s)" \
  || echo "" && echo "⚠ [Stage 1/$STAGES] Quality gate failed — continuing ($(( SECONDS - stage_start ))s)"
echo ""

# Stage 2/3: Simulate
stage_start=$SECONDS
echo "▶ [Stage 2/$STAGES] Simulate — 5000 paired trials"
if [ -f "$OUTDIR/e-vs-f-distributions.json" ]; then
  /home/xeal/.local/bin/micromamba run -n alethic python scripts/e_vs_f_simulate.py \
    --sweep -n 5000 -t 2000 \
    -d "$OUTDIR/e-vs-f-distributions.json" \
    -o "docs/results/$SLUG/e-vs-f-report.md" \
    && echo "✓ [Stage 2/$STAGES] DONE ($(( SECONDS - stage_start ))s)" \
    || echo "⚠ [Stage 2/$STAGES] Failed — continuing ($(( SECONDS - stage_start ))s)"
else
  echo "⚠ [Stage 2/$STAGES] SKIPPED (no distributions file)"
fi
echo ""

# Stage 3/3: Breadth vs Depth
stage_start=$SECONDS
echo "▶ [Stage 3/$STAGES] Breadth vs Depth — 20 problems × 5 reps"
echo "  Depth:   1 run × 12 iters"
echo "  Breadth: 6 runs × 2 iters"
echo ""
/home/xeal/.local/bin/micromamba run -n alethic python scripts/breadth_vs_depth.py \
  -m "$MODEL" --depth-iters 12 --breadth-runs 6 -r 5 \
  -o "$OUTDIR/bvd.json"
echo ""
echo "✓ [Stage 3/$STAGES] DONE ($(( SECONDS - stage_start ))s)"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Qwen 3.6: ALL STAGES COMPLETE                     ║"
echo "║  Total time: $(( SECONDS - pipeline_start ))s                                       ║"
echo "║  Results: $OUTDIR/                       ║"
echo "╚══════════════════════════════════════════════════════════╝"
