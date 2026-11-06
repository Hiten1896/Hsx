#!/usr/bin/env bash
set -u

PORT="${HSX_PORT:-8000}"
failures=0

report() {
  local name="$1"
  shift
  if "$@"; then
    printf 'OK: %s\n' "$name"
  else
    printf 'CHECK: %s needs attention\n' "$name"
    failures=$((failures + 1))
  fi
}

report "hsx.service" systemctl is-active --quiet hsx.service
report "port ${PORT}" bash -c "ss -ltn '( sport = :${PORT} )' | grep -q LISTEN"
report "health endpoint" curl --fail --silent --show-error "http://127.0.0.1:${PORT}/healthz"
report "tailscaled" systemctl is-active --quiet tailscaled.service
report "Tailscale serve" tailscale serve status
report "disk usage below 85%" bash -c "df -P / | awk 'NR == 2 { gsub(/%/, \"\", \$5); exit (\$5 >= 85) }'"
report "clock synchronization" bash -c "timedatectl show -p NTPSynchronized --value | grep -qx yes"

if (( failures > 0 )); then
  printf '%s check(s) need attention.\n' "$failures"
  exit 1
fi
