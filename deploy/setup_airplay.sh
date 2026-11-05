#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root: sudo bash deploy/setup_airplay.sh" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer requires Debian's apt-get package manager." >&2
  exit 1
fi

raw_name="${HSX_AIRPLAY_NAME:-HSX-$(hostname -s)}"
if [[ ! "${raw_name}" =~ ^[A-Za-z0-9._\ -]{1,63}$ ]]; then
  echo "HSX_AIRPLAY_NAME may contain only letters, numbers, spaces, dot, underscore, and hyphen (max 63)." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y shairport-sync avahi-daemon alsa-utils

config="/etc/shairport-sync.conf"
if [[ -f "${config}" && ! -f "${config}.hsx-backup" ]]; then
  cp -a "${config}" "${config}.hsx-backup"
fi
temporary="$(mktemp "${config}.XXXXXX")"
trap 'rm -f "${temporary}"' EXIT
cat > "${temporary}" <<EOF
general =
{
  name = "${raw_name}";
};

alsa =
{
  output_device = "default";
};
EOF
install -o root -g root -m 0644 "${temporary}" "${config}"

systemctl enable --now avahi-daemon.service
systemctl enable --now shairport-sync.service
systemctl restart shairport-sync.service

echo "Shairport Sync is enabled as '${raw_name}'."
echo "Ensure the Wi-Fi network permits mDNS/AirPlay discovery and the service account can access ALSA."
