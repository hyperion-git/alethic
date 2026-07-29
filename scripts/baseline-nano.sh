#!/usr/bin/env bash
set -uo pipefail
cd ~/dev/alethic
MODEL="nvidia/nemotron-3-nano-30b-a3b:free"
SLUG="nano"
OUTFILE="data/calibration/$SLUG/baseline.jsonl"
mkdir -p "data/calibration/$SLUG"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Baseline: Nemotron Nano — raw solve + self-judge       ║"
echo "║  20 problems, no Alethic harness                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

/home/xeal/.local/bin/micromamba run -n alethic python scripts/baseline_raw.py \
  -m "$MODEL" -o "$OUTFILE" -r 20 --resume

echo ""
echo "✓ Baseline complete: $OUTFILE"
