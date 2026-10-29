#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
VENV_DIR="${HSX_VENV_DIR:-${REPO_ROOT}/venv}"
VENV_ACTIVATE="${VENV_DIR}/bin/activate"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing environment file: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "Missing virtual environment activation script: ${VENV_ACTIVATE}" >&2
  exit 1
fi

cd "${REPO_ROOT}"
source "${VENV_ACTIVATE}"
exec python main.py
