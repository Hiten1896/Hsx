#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "This script must be run as root. Example: sudo bash scripts/setup_systemd.sh" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN_FILE="${REPO_ROOT}/main.py"
ENV_FILE="${REPO_ROOT}/.env"
VENV_ACTIVATE="${REPO_ROOT}/venv/bin/activate"
RUNNER="${REPO_ROOT}/scripts/run_hsx.sh"
SERVICE_NAME="${HSX_SERVICE_NAME:-hsx}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TTY_OVERRIDE_DIR="/etc/systemd/system/getty@tty1.service.d"
TTY_OVERRIDE_FILE="${TTY_OVERRIDE_DIR}/override.conf"
LOG_DIR="${REPO_ROOT}/logs"
LOG_FILE="${LOG_DIR}/hsx_service.log"
AUTO_LOGIN_USER="${HSX_AUTO_LOGIN_USER:-${SUDO_USER:-$(logname 2>/dev/null || whoami)}}"

if [[ ! -f "${MAIN_FILE}" ]]; then
  echo "Missing project entrypoint: ${MAIN_FILE}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing environment file: ${ENV_FILE}. Copy .env.example to .env first." >&2
  exit 1
fi

if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "Missing virtual environment activation script: ${VENV_ACTIVATE}. Create the venv first." >&2
  exit 1
fi

if [[ ! -f "${RUNNER}" ]]; then
  echo "Missing service runner: ${RUNNER}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
touch "${LOG_FILE}"
chown "${AUTO_LOGIN_USER}:$(id -gn "${AUTO_LOGIN_USER}")" "${LOG_FILE}"
chmod 0640 "${LOG_FILE}"

cat > "${SERVICE_FILE}.tmp" <<EOF
[Unit]
Description=Hsx local AI workspace agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${AUTO_LOGIN_USER}
WorkingDirectory=${REPO_ROOT}
Environment=PYTHONUNBUFFERED=1
Environment=HSX_VENV_DIR=${REPO_ROOT}/venv
ExecStart=${RUNNER}
Restart=always
RestartSec=5
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}
NoNewPrivileges=false
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

install -o root -g root -m 0644 "${SERVICE_FILE}.tmp" "${SERVICE_FILE}"
rm -f "${SERVICE_FILE}.tmp"

mkdir -p "${TTY_OVERRIDE_DIR}"
cat > "${TTY_OVERRIDE_FILE}.tmp" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${AUTO_LOGIN_USER} --noclear %I linux
EOF
install -o root -g root -m 0644 "${TTY_OVERRIDE_FILE}.tmp" "${TTY_OVERRIDE_FILE}"
rm -f "${TTY_OVERRIDE_FILE}.tmp"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"
systemctl enable "getty@tty1.service"

echo
echo "Hsx service installed: ${SERVICE_FILE}"
echo "TTY1 autologin configured: ${TTY_OVERRIDE_FILE}"
echo "TTY1 auto-login will take effect on the next boot."
echo "Logs: ${LOG_FILE}"
echo "To check status: systemctl status ${SERVICE_NAME}.service"
echo "To view logs: journalctl -u ${SERVICE_NAME}.service -f"
