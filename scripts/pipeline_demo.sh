#!/usr/bin/env bash
# The local data pipeline end to end: capture → move → parse/validate → store → analyse.
#
#   ./scripts/pipeline_demo.sh
#
# 1. The device runs with capture on; AutoLab runs a 15-experiment campaign on it.
# 2. The uploader starts while the ingest is DOWN: lines wait in the capture log.
# 3. The ingest comes up; the uploader drains the backlog.
# 4. The whole log is replayed: every message is recognised as a duplicate.
# 5. Bad lines (old schema, torn write, impossible value) are sent: quarantined.
# 6. The pipeline's own analysis is compared with AutoLab's QC verdicts.
# Everything is written under runs/pipeline-demo/ (git-ignored).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=${PORT:-8751}
INGEST_PORT=${INGEST_PORT:-8761}
EXPERIMENTS=${EXPERIMENTS:-15}
OUT=runs/pipeline-demo
rm -rf "$OUT" && mkdir -p "$OUT"
uv sync --all-extras --quiet
AUTOLAB=.venv/bin/autolab
PY=.venv/bin/python
CAPTURE="$OUT/device/capture"

cleanup() {
  pkill -TERM -f "algae_sim device serve --port $PORT" 2>/dev/null || true
  pkill -TERM -f "algae_sim pipeline ingest --port $INGEST_PORT" 2>/dev/null || true
  pkill -TERM -f "algae_sim pipeline upload --capture $CAPTURE" 2>/dev/null || true
}
trap cleanup EXIT

PORT="$PORT" EXPERIMENTS="$EXPERIMENTS" "$PY" - <<'PYEOF'
import os, yaml
c = yaml.safe_load(open("configs/culture-device.yaml"))
c["execution"]["controller"]["settings"]["url"] = f"http://127.0.0.1:{os.environ['PORT']}"
c["campaign"]["max_experiments"] = int(os.environ["EXPERIMENTS"])
open("runs/pipeline-demo/campaign.yaml", "w").write(yaml.safe_dump(c))
PYEOF

echo "1. Device (lockstep, capture on) and an AutoLab campaign of $EXPERIMENTS experiments"
"$PY" -m algae_sim device serve --port "$PORT" --settings configs/device-settings.json \
  --state "$OUT/device" --capture "$CAPTURE" > "$OUT/device.log" 2>&1 &
disown
sleep 1

echo "2. Uploader starts; the ingest isn't running yet"
"$PY" -m algae_sim pipeline upload --capture "$CAPTURE" --url "http://127.0.0.1:$INGEST_PORT" \
  --follow --exit-when-idle 3 > "$OUT/uploader.log" 2>&1 &
UPLOADER=$!

COMMON=(--database "$OUT/autolab.sqlite3" --policy configs/culture-policy.yaml
        --optimizer-root "$OUT/optimizer")
"$AUTOLAB" init --database "$OUT/autolab.sqlite3" > /dev/null
CID=$("$AUTOLAB" culture create "$OUT/campaign.yaml" "${COMMON[@]}" --id-only)
"$AUTOLAB" culture run "$CID" "${COMMON[@]}" --artifact-root "$OUT/artifacts" --quiet
echo "   campaign done; capture log has $(wc -l < "$CAPTURE/telemetry.jsonl" | tr -d ' ') lines"
echo "   uploader meanwhile: $(grep -c unavailable "$OUT/uploader.log" || true) failed attempts, latest:"
echo "     $(grep unavailable "$OUT/uploader.log" | tail -1 | sed -E 's/ \(.*\);/,/')"

echo "3. Ingest comes up; the uploader drains the backlog"
"$PY" -m algae_sim pipeline ingest --port "$INGEST_PORT" --data "$OUT/pipeline" \
  > "$OUT/ingest.log" 2>&1 &
disown
wait "$UPLOADER"
{ grep -E "^sent" "$OUT/uploader.log" || true; } | awk '{l+=$2; s+=$4; d+=$6} END {
  printf "   %d batches, %d lines: %d stored, %d duplicates\n", NR, l, s, d}'

echo "4. Replay the whole log (as if the uploader lost its cursor)"
"$PY" -m algae_sim pipeline upload --capture "$CAPTURE" \
  --url "http://127.0.0.1:$INGEST_PORT" --replay | awk '{l+=$2; s+=$4; d+=$6} END {
  printf "   %d lines resent: %d stored, %d recognised as duplicates\n", l, s, d}'

echo "5. Bad data arrives: an old-schema line, a torn write and an impossible reading"
pkill -TERM -f "algae_sim device serve --port $PORT"; sleep 0.5
"$PY" - "$CAPTURE/telemetry.jsonl" <<'PYEOF'
import json, sys
path = sys.argv[1]
last = next(m for m in map(json.loads, reversed(open(path).readlines()))
            if m["kind"] == "environment")
bad_value = {**last, "sequence": 10**6, "measurements": {"temperature_c": 412.0}}
with open(path, "a") as f:
    f.write(json.dumps({**last, "schema_version": "1"}) + "\n")
    f.write(json.dumps(last)[:60] + "\n")
    f.write(json.dumps(bad_value) + "\n")
PYEOF
"$PY" -m algae_sim pipeline upload --capture "$CAPTURE" --url "http://127.0.0.1:$INGEST_PORT" \
  | sed 's/^/   /'

echo "6. What the pipeline concludes from its own stored data"
"$PY" -m algae_sim pipeline report --data "$OUT/pipeline" | sed 's/^/   /'
echo
echo "   AutoLab's QC failures, for comparison:"
"$PY" - "$OUT/autolab.sqlite3" <<'PYEOF'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
for ordinal, name, reactor in db.execute(
    "select e.ordinal, c.name, json_extract(c.details, '$.reactor_id') as reactor from qc_checks c "
    "join qc_runs q on q.id = c.qc_run_id join experiments e on e.id = q.experiment_id "
    "where c.status != 'passed' order by e.ordinal, reactor"):
    print(f"     hold {ordinal:>2}  {reactor}  {name}")
PYEOF
