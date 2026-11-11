#!/usr/bin/env bash
# One-command demo: a full AutoLab culture campaign on the simulated reactor.
# 20 experiments x 3 replicate reactors = 60 simulated 36 h cultures, about 15 s
# of simulation once dependencies are installed.
# Writes everything under runs/demo/ (git-ignored) and draws charts into runs/demo/charts/.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=runs/demo
rm -rf "$OUT" && mkdir -p "$OUT"
COMMON=(--database "$OUT/autolab.sqlite3" --policy configs/culture-policy.yaml)

uv sync --all-extras --quiet
uv run autolab init --database "$OUT/autolab.sqlite3" > /dev/null
CID=$(uv run autolab culture create configs/culture-algae-sim.yaml "${COMMON[@]}" \
  --optimizer-root "$OUT/optimizer" --id-only)
echo "campaign $CID"
uv run autolab culture run "$CID" "${COMMON[@]}" \
  --optimizer-root "$OUT/optimizer" --artifact-root "$OUT/artifacts" 2>&1 \
  | grep -v "study created"
uv run autolab culture report "$CID" "${COMMON[@]}" > "$OUT/report.json"
uv run python -m algae_sim.autolab.plot_campaign "$OUT/autolab.sqlite3" "$OUT/charts"
echo "report: $OUT/report.json"
