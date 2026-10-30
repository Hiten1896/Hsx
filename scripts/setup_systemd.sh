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
UPDATE_SERVICE_FILE="/etc/systemd/system/hsx-update.service"
UPDATE_TIMER_FILE="/etc/systemd/system/hsx-update.timer"
TTY_OVERRIDE_DIR="/etc/systemd/system/getty@tty1.service.d"
TTY_OVERRIDE_FILE="${TTY_OVERRIDE_DIR}/override.conf"
LOG_DIR="${REPO_ROOT}/logs"
LOG_FILE="${LOG_DIR}/hsx_service.log"
AUTO_LOGIN_USER="${HSX_AUTO_LOGIN_USER:-${SUDO_USER:-$(logname 2>/dev/null || whoami)}}"
AUTO_LOGIN_HOME="$(getent passwd "${AUTO_LOGIN_USER}" | cut -d: -f6)"

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

if [[ -z "${AUTO_LOGIN_HOME}" || ! -d "${AUTO_LOGIN_HOME}" ]]; then
  echo "Could not resolve a home directory for ${AUTO_LOGIN_USER}." >&2
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
EnvironmentFile=${ENV_FILE}
ExecStart=/bin/bash ${RUNNER}
Restart=on-failure
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

cat > "${UPDATE_SERVICE_FILE}.tmp" <<EOF
[Unit]
Description=Check and apply trusted HSX source updates
After=network-online.target ${SERVICE_NAME}.service
Wants=network-online.target

[Service]
Type=oneshot
User=${AUTO_LOGIN_USER}
WorkingDirectory=${REPO_ROOT}
EnvironmentFile=${ENV_FILE}
Environment=HSX_REPO_DIR=${REPO_ROOT}
Environment=HSX_VENV_DIR=${REPO_ROOT}/venv
Environment=HSX_SERVICE_NAME=${SERVICE_NAME}
Environment=PIP_NO_CACHE_DIR=1
ExecStart=${REPO_ROOT}/venv/bin/python ${REPO_ROOT}/scripts/update_hsx.py
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF
install -o root -g root -m 0644 "${UPDATE_SERVICE_FILE}.tmp" "${UPDATE_SERVICE_FILE}"
rm -f "${UPDATE_SERVICE_FILE}.tmp"

install -o root -g root -m 0644 "${REPO_ROOT}/deploy/hsx-update.timer" "${UPDATE_TIMER_FILE}"

mkdir -p "${TTY_OVERRIDE_DIR}"
cat > "${TTY_OVERRIDE_FILE}.tmp" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${AUTO_LOGIN_USER} --noclear %I linux
EOF
install -o root -g root -m 0644 "${TTY_OVERRIDE_FILE}.tmp" "${TTY_OVERRIDE_FILE}"
rm -f "${TTY_OVERRIDE_FILE}.tmp"

PROFILE_MARKER="# >>> HSX TTY1 CLI >>>"
install_tty_cli_hook() {
  local profile_file="$1"
  if grep -Fq "${PROFILE_MARKER}" "${profile_file}" 2>/dev/null; then
    return
  fi
  SHELL_REPO_ROOT="$(printf '%q' "${REPO_ROOT}")"
  cat >> "${profile_file}" <<EOF

${PROFILE_MARKER}
if [[ "\$(tty 2>/dev/null)" == "/dev/tty1" ]]; then
  HSX_REPO_ROOT=${SHELL_REPO_ROOT}
  cd "\${HSX_REPO_ROOT}"
  source "\${HSX_REPO_ROOT}/venv/bin/activate"
  exec python "\${HSX_REPO_ROOT}/cli.py"
fi
# <<< HSX TTY1 CLI <<<
EOF
  chown "${AUTO_LOGIN_USER}:$(id -gn "${AUTO_LOGIN_USER}")" "${profile_file}"
}

install_tty_cli_hook "${AUTO_LOGIN_HOME}/.profile"
if [[ -f "${AUTO_LOGIN_HOME}/.bash_profile" ]]; then
  install_tty_cli_hook "${AUTO_LOGIN_HOME}/.bash_profile"
fi
if [[ -f "${AUTO_LOGIN_HOME}/.bash_login" ]]; then
  install_tty_cli_hook "${AUTO_LOGIN_HOME}/.bash_login"
fi

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"
systemctl enable --now hsx-update.timer
systemctl enable "getty@tty1.service"

echo
echo "Hsx service installed: ${SERVICE_FILE}"
echo "HSX update timer installed: ${UPDATE_TIMER_FILE} (disabled until explicitly configured)"
echo "TTY1 autologin configured: ${TTY_OVERRIDE_FILE}"
echo "TTY1 auto-login will take effect on the next boot."
echo "TTY1 shell profile launches the interactive HSX CLI."
echo "Logs: ${LOG_FILE}"
echo "To check status: systemctl status ${SERVICE_NAME}.service"
echo "To view logs: journalctl -u ${SERVICE_NAME}.service -f"
