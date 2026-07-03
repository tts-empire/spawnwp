# Local aggregate metrics for SpawnWP (/var/lib/spawnwp/metrics.json).
# Counters only — no names, domains or content. Always collected (they are
# useful locally); they leave the machine only inside the anonymous telemetry
# heartbeat, and only for notice-v3 consents. Writers hold an exclusive flock
# so concurrent scripts never lose increments. Best-effort: a metrics failure
# must never break the calling script.

SPAWNWP_METRICS_FILE="${SPAWNWP_METRICS_FILE:-/var/lib/spawnwp/metrics.json}"

# metric_incr <key> [n=1]
metric_incr() {
  _metric_update "$1" "${2:-1}" "" || true
}

# metric_duration <bucket> <seconds>
# Updates <bucket>_count, <bucket>_seconds_sum and <bucket>_seconds_max.
metric_duration() {
  _metric_update "" "" "$1:$2" || true
}

_metric_update() {
  local key="$1" n="$2" duration="$3"
  mkdir -p "$(dirname "$SPAWNWP_METRICS_FILE")" 2>/dev/null || return 0
  (
    flock -w 5 9 || exit 0
    METRIC_KEY="$key" METRIC_N="$n" METRIC_DURATION="$duration" \
      METRIC_FILE="$SPAWNWP_METRICS_FILE" python3 - <<'PYEOF' 2>/dev/null
import json, os
path = os.environ["METRIC_FILE"]
try:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
except (OSError, ValueError):
    data = {}
key, n, duration = os.environ["METRIC_KEY"], os.environ["METRIC_N"], os.environ["METRIC_DURATION"]
if key:
    data[key] = int(data.get(key, 0)) + int(n)
elif duration:
    bucket, _, seconds = duration.partition(":")
    seconds = int(seconds)
    data[f"{bucket}_count"] = int(data.get(f"{bucket}_count", 0)) + 1
    data[f"{bucket}_seconds_sum"] = int(data.get(f"{bucket}_seconds_sum", 0)) + seconds
    data[f"{bucket}_seconds_max"] = max(int(data.get(f"{bucket}_seconds_max", 0)), seconds)
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, sort_keys=True)
os.replace(tmp, path)
PYEOF
  ) 9>"${SPAWNWP_METRICS_FILE}.lock" 2>/dev/null || true
}
