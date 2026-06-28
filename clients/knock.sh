#!/usr/bin/env bash
# Send a SpawnWP TCP port-knock sequence.
set -u

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <host> <port> [port ...]" >&2
  exit 1
fi

host="$1"
shift

knock_port() {
  local h="$1" p="$2"
  if command -v ncat >/dev/null 2>&1; then
    ncat -w 1 "$h" "$p" </dev/null >/dev/null 2>&1
  elif command -v nc >/dev/null 2>&1; then
    nc -w 1 "$h" "$p" </dev/null >/dev/null 2>&1
  else
    timeout 1 bash -c "exec 3<>/dev/tcp/$h/$p" >/dev/null 2>&1
  fi
  return 0
}

for port in "$@"; do
  echo "knock → ${host}:${port}"
  knock_port "$host" "$port"
  sleep 1
done

echo "Done. If the sequence was correct, your IP is now allowed."
echo "Open the cockpit: https://${host}/"
