#!/usr/bin/env bash
# Install Hsx as an always-on service on Debian/Ubuntu. Run from anywhere as your normal user.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not root (it uses sudo where needed)." >&2
  exit 1
fi
command -v apt-get >/dev/null 2>&1 || { echo "This script supports Debian/Ubuntu only." >&2; exit 1; }

echo "==> System packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git ffmpeg v4l-utils openssh-server tmux curl logrotate

echo "==> Python environment in $DIR/.venv"
cd "$DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
if [ -f requirements.txt ]; then .venv/bin/pip install -r requirements.txt; fi

echo "==> Folders and permissions"
WORKSPACE="$(grep -E '^HSX_WORKSPACE=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true)"
WORKSPACE="${WORKSPACE:-/home/hsxshare}"
sudo mkdir -p "$WORKSPACE"
sudo chown "$RUN_USER":"$RUN_USER" "$WORKSPACE"
mkdir -p "$HOME/hsx-projects" logs data
if [ -f .env ]; then chmod 600 .env; fi
[ -f data/auth.token ] && chmod 600 data/auth.token || true

echo "==> Device groups (camera, audio, serial). Log out and in once for them to apply."
sudo usermod -aG video,audio,dialout "$RUN_USER" || true

echo "==> Ollama service limits"
if systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
  sudo install -d /etc/systemd/system/ollama.service.d
  sudo install -m 644 deploy/ollama-override.conf /etc/systemd/system/ollama.service.d/override.conf
  OLLAMA_SERVICE=1
else
  echo "ollama.service not found; Hsx will start 'ollama serve' itself when needed."
  OLLAMA_SERVICE=0
fi

echo "==> Hsx service and log rotation"
sed -e "s|__USER__|$RUN_USER|g" -e "s|__DIR__|$DIR|g" deploy/hsx.service | sudo tee /etc/systemd/system/hsx.service >/dev/null
sed -e "s|__USER__|$RUN_USER|g" -e "s|__DIR__|$DIR|g" deploy/hsx.logrotate | sudo tee /etc/logrotate.d/hsx >/dev/null
sudo systemctl daemon-reload
if [ "$OLLAMA_SERVICE" -eq 1 ]; then sudo systemctl enable ollama && sudo systemctl restart ollama; fi
sudo systemctl enable --now hsx

echo
echo "Done. Useful commands:"
echo "  systemctl status hsx          journalctl -u hsx -f"
echo "  sudo systemctl restart hsx    (stops running background jobs; restart when idle)"
echo "Remote access: keep HSX_HOST=127.0.0.1 and run   sudo tailscale serve --bg 8000"
