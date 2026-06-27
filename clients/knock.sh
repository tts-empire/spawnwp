#!/usr/bin/env bash
#
# spawnwp — portable port-knock client.
#
# Sends a TCP SYN to each port in order to open (or close) the cockpit firewall
# allow-list for your IP. The ports come from YOUR install's credentials report
# (/root/spawnwp-credentials.txt) — they are random per install.
#
# Usage:
#   ./knock.sh <host> <port> [port ...]
#
# Examples (numbers are placeholders — use your own sequence):
#   ./knock.sh cockpit.example.com 12345 23456 34567     # open
#   ./knock.sh cockpit.example.com 34567 23456 12345     # close (reverse order)
#
# It tries, in order: ncat, nc (-w1), then bash's /dev/tcp.
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
    # Pure-bash fallback (no nc available). Connection is expected to fail/time out.
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
echo "Open the cockpit: https://${host}/   (HTTP Basic Auth required)"
