#!/usr/bin/env bash
# One-time setup: create ./.venv, install the Python packages, check gbrain.
# Safe to run again. Usage: ./setup.sh [python-executable]   (default: python3.12, python3.13, python3)
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${1:-}"
if [ -z "$PYTHON" ]; then
  for c in python3.12 python3.13 python3.14 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
      PYTHON="$c"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "Python 3.12 or newer is required. On Ubuntu: sudo apt-get install python3.12 python3.12-venv" >&2
  exit 1
fi
echo "Using $($PYTHON --version) at $(command -v "$PYTHON")"
[ -d .venv ] || "$PYTHON" -m venv .venv || {
  echo "Creating the virtual environment failed. On Ubuntu: sudo apt-get install python3-venv" >&2
  exit 1
}
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet --prefer-binary -r requirements.txt
echo "Python packages installed in ./.venv"
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Activated the virtual environment ($VIRTUAL_ENV)"
if [ ! -f .env.local ]; then
  cat > .env.local <<'ENV'
# Local settings for ingest.sh (this file is not committed). Remove the # to enable a line.
#GBRAIN_HOME=/home/ubuntu/docingest-brain
#GBRAIN_BIN=/home/ubuntu/.bun/bin/gbrain
#LITELLM_BASE_URL=https://your-proxy.example.com/v1
#LITELLM_API_KEY=replace-me
#EMBEDDING_MODEL=name-the-proxy-lists
#LLM_MODEL=gpt-5-mini-ai
ENV
  chmod 600 .env.local
  echo "Created .env.local; edit it to add your LiteLLM URL and key"
fi
echo
./ingest.sh doctor || {
  echo
  echo "Fix the FAIL lines above, then run ./ingest.sh doctor again."
  exit 1
}
echo
echo "A script cannot activate the environment in your terminal. To do that, run:"
echo "  source .venv/bin/activate"
