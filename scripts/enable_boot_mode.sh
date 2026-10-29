#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root: sudo bash scripts/enable_boot_mode.sh" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
VENV_DIR="${REPO_ROOT}/venv"
AUTO_LOGIN_USER="${HSX_AUTO_LOGIN_USER:-${SUDO_USER:-$(logname 2>/dev/null || whoami)}}"
SERVICE_NAME="${HSX_SERVICE_NAME:-hsx}"
export HSX_SERVICE_NAME="${SERVICE_NAME}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}; copy .env.example to .env before enabling boot mode." >&2
  exit 1
fi
if [[ ! -x "${VENV_DIR}/bin/python" || ! -f "${VENV_DIR}/bin/activate" ]]; then
  echo "Missing ${VENV_DIR}; create the project virtual environment first." >&2
  exit 1
fi
if ! id "${AUTO_LOGIN_USER}" >/dev/null 2>&1; then
  echo "Unknown HSX user: ${AUTO_LOGIN_USER}" >&2
  exit 1
fi

VOICE_ENABLED="$(grep -E '^HSX_ENABLE_VOICE_LISTENER=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2- || true)"
if [[ "${VOICE_ENABLED}" != "1" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Persistent microphone capture needs explicit consent. Run interactively or set HSX_ENABLE_VOICE_LISTENER=1 in .env first." >&2
    exit 1
  fi
  echo "This enables a microphone listener at every boot."
  echo "It listens for the configured wake phrase, transcribes locally, and discards captured audio."
  echo "The installer may install host audio/video packages and download the Whisper model."
  read -r -p "Enable persistent microphone listening? Type YES to continue: " confirmation
  if [[ "${confirmation}" != "YES" ]]; then
    echo "Boot mode setup cancelled; microphone listening was not enabled."
    exit 1
  fi
fi

ENV_OWNER="$(stat -c '%u:%g' "${ENV_FILE}")"
ENV_MODE="$(stat -c '%a' "${ENV_FILE}")"
if grep -q '^HSX_ENABLE_VOICE_LISTENER=' "${ENV_FILE}"; then
  sed -i 's/^HSX_ENABLE_VOICE_LISTENER=.*/HSX_ENABLE_VOICE_LISTENER=1/' "${ENV_FILE}"
else
  printf '\nHSX_ENABLE_VOICE_LISTENER=1\n' >> "${ENV_FILE}"
fi
chown "${ENV_OWNER}" "${ENV_FILE}"
chmod "${ENV_MODE}" "${ENV_FILE}"

missing_packages=()
for package in libportaudio2 ffmpeg mpv yt-dlp; do
  if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q 'install ok installed'; then
    missing_packages+=("${package}")
  fi
done
if ((${#missing_packages[@]})); then
  apt-get update
  apt-get install -y "${missing_packages[@]}"
fi

if ! runuser -u "${AUTO_LOGIN_USER}" -- "${VENV_DIR}/bin/python" -c 'import numpy, sounddevice, webrtcvad, whisper' >/dev/null 2>&1; then
  echo "Installing optional local Whisper, microphone, and VAD dependencies..."
  runuser -u "${AUTO_LOGIN_USER}" -- "${VENV_DIR}/bin/python" -m pip install -r "${REPO_ROOT}/requirements-voice.txt"
fi

echo "Checking/downloading the configured local Whisper model as ${AUTO_LOGIN_USER}..."
runuser -u "${AUTO_LOGIN_USER}" -- env HSX_PROJECT_ROOT="${REPO_ROOT}" "${VENV_DIR}/bin/python" -c '
import os
from pathlib import Path
from dotenv import dotenv_values
settings = dotenv_values(Path(os.environ["HSX_PROJECT_ROOT"]) / ".env")
model = settings.get("HSX_VOICE_MODEL", "base")
import whisper
whisper.load_model(model)
'

for group in audio video; do
  if getent group "${group}" >/dev/null && ! id -nG "${AUTO_LOGIN_USER}" | tr ' ' '\n' | grep -qx "${group}"; then
    usermod -aG "${group}" "${AUTO_LOGIN_USER}"
  fi
done

bash "${REPO_ROOT}/scripts/setup_systemd.sh"

VOICE_SERVICE="/etc/systemd/system/hsx-voice-listener.service"
cat > "${VOICE_SERVICE}.tmp" <<EOF
[Unit]
Description=Hsx opt-in local voice command listener
After=${SERVICE_NAME}.service sound.target
Requires=${SERVICE_NAME}.service

[Service]
Type=simple
User=${AUTO_LOGIN_USER}
WorkingDirectory=${REPO_ROOT}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/python ${REPO_ROOT}/scripts/voice_listener.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
install -o root -g root -m 0644 "${VOICE_SERVICE}.tmp" "${VOICE_SERVICE}"
rm -f "${VOICE_SERVICE}.tmp"

systemctl daemon-reload
systemctl enable --now hsx-voice-listener.service

echo
echo "HSX boot mode enabled:"
echo "  Server: systemctl status ${SERVICE_NAME}.service"
echo "  Voice:  systemctl status hsx-voice-listener.service"
echo "  TTY1:   auto-login followed by the HSX prompt CLI"
echo "  Logs:   journalctl -u hsx-voice-listener.service -f"
