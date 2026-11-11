#!/usr/bin/env bash
# Crash AutoLab in the middle of a culture, keep the device running, then resume.
#
#   ./scripts/resume_demo.sh                 # restart straight away (brief outage)
#   OUTAGE_S=60 ./scripts/resume_demo.sh     # stay down 60 more real s (~15 simulated h)
#
# The device runs at SPEED x real time (900: a 36 h culture takes ~2.4 min). AutoLab is
# killed with SIGKILL, like a crash or power cut: no safe state, no clean shutdown.
# Everything is written under runs/resume/ (git-ignored).
set -euo pipefail
cd "$(dirname "$0")/.."

SPEED=${SPEED:-900}
KILL_AT_H=${KILL_AT_H:-13}
OUTAGE_S=${OUTAGE_S:-0}
PORT=${PORT:-8750}
OUT=runs/resume
rm -rf "$OUT" && mkdir -p "$OUT"
uv sync --all-extras --quiet
AUTOLAB=.venv/bin/autolab
PY=.venv/bin/python

status() { curl -s "localhost:$PORT/v1/status"; }
show() {
  status | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
for k, v in d['reactors'].items():
    print(f\"    {k}: {v['state']:8s} hold {v.get('hold_elapsed_h', 0):6.2f} h, \"
          f\"measured {v.get('measured', {}).get('temperature')} °C, \"
          f\"pH {v.get('measured', {}).get('ph')}\")"
}
cleanup() { pkill -TERM -f "algae_sim device serve --port $PORT" 2>/dev/null || true; }
trap cleanup EXIT

# One experiment is enough to show the resume. At high speed, AutoLab's restart (a few
# real seconds) is hours of culture time, so this demo's QC tolerates a short gap in
# temperature telemetry (75% coverage instead of AutoLab's default 90%). A longer
# OUTAGE_S still fails it, which is the point: AutoLab won't vouch for a hold it
# didn't watch for long enough.
PORT="$PORT" "$PY" - <<'PYEOF'
import os, yaml
c = yaml.safe_load(open("configs/culture-device.yaml"))
c["execution"]["controller"]["settings"]["url"] = f"http://127.0.0.1:{os.environ['PORT']}"
c["campaign"]["max_experiments"] = 1
c["optimization"]["startup_experiments"] = 1
c["culture_qc"]["minimum_telemetry_coverage"] = 0.75
open("runs/resume/campaign.yaml", "w").write(yaml.safe_dump(c))
PYEOF

echo "1. Starting the device at ${SPEED}x real time"
"$PY" -m algae_sim device serve --port "$PORT" --speed "$SPEED" \
  --settings configs/device-settings.json --state "$OUT/device" > "$OUT/device.log" 2>&1 &
sleep 1.5

COMMON=(--database "$OUT/autolab.sqlite3" --policy configs/culture-policy.yaml
        --optimizer-root "$OUT/optimizer" --artifact-root "$OUT/artifacts")
"$AUTOLAB" init --database "$OUT/autolab.sqlite3" > /dev/null
CID=$("$AUTOLAB" culture create "$OUT/campaign.yaml" --database "$OUT/autolab.sqlite3" \
  --policy configs/culture-policy.yaml --optimizer-root "$OUT/optimizer" --id-only)

echo "2. AutoLab starts the culture (campaign $CID)"
"$AUTOLAB" culture run "$CID" "${COMMON[@]}" > "$OUT/run-before-crash.log" 2>&1 &
APID=$!
until status | "$PY" -c "
import json, sys
r = json.load(sys.stdin)['reactors']['reactor-1']
sys.exit(0 if r['state'] == 'holding' and r['hold_elapsed_h'] >= $KILL_AT_H else 1)"; do
  sleep 0.1
done

kill -9 "$APID"
echo "3. AutoLab killed (SIGKILL) ${KILL_AT_H} h into the hold. The device keeps going:"
show
if [ "$OUTAGE_S" -gt 0 ]; then
  sleep "$OUTAGE_S"
  echo "   ...after ${OUTAGE_S} s more of outage:"
  show
fi

echo "4. Restarting AutoLab: it confirms each reactor still holds its command, then resumes"
"$AUTOLAB" culture run "$CID" "${COMMON[@]}" 2>&1 | grep -E "^(state|experiment_count|observed|qc_failed):"

echo "5. What AutoLab recorded"
"$PY" - "$OUT/autolab.sqlite3" <<'PYEOF'
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
events = db.execute(
    "select json_extract(response_snapshot, '$.code'), "
    "json_extract(response_snapshot, '$.reactor_id') from action_events "
    "where event_type = 'event' order by sequence").fetchall()
print("    run events:", ", ".join(f"{c}{'@' + r if r else ''}" for c, r in events) or "none")
from datetime import datetime
times = [datetime.fromisoformat(t) for (t,) in db.execute(
    "select json_extract(response_snapshot, '$.sampled_at') from action_events "
    "where event_type = 'telemetry' and json_extract(response_snapshot, '$.reactor_id') = "
    "'reactor-1' order by sequence")]
gap = max((b - a for a, b in zip(times, times[1:])), default=None)
if gap is not None:
    print(f"    longest gap AutoLab wasn't watching: {gap.total_seconds() / 3600:.1f} simulated h")
missing = {}
for eid, rid, err in db.execute(
    "select json_extract(response_snapshot, '$.expected_measurement_id'), "
    "json_extract(response_snapshot, '$.request.sample_id'), "
    "json_extract(response_snapshot, '$.error_code') from action_events "
    "where event_type = 'reading' order by sequence"):
    missing.setdefault(rid, []).append(eid)
print("    OD readings taken per reactor:", {k: len(v) for k, v in missing.items()}, "of 6 planned")
for name, status, details in db.execute(
    "select c.name, c.status, c.details from qc_checks c order by c.name"):
    if status != "passed":
        d = json.loads(details)
        print(f"    QC failed: {name} on {d.get('reactor_id')}: {d.get('message')}")
print("    experiment:", db.execute("select state from experiments").fetchone()[0])
PYEOF
