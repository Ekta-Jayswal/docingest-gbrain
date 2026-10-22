#!/usr/bin/env bash
# Run docingest from this folder. See COMMANDS.md for every command.
#   ./ingest.sh doctor                         check this machine
#   ./ingest.sh ingest <file-or-folder> [--embed]
#   ./ingest.sh selftest [--full]              did a change break anything? (quick | everything)
#   ./ingest.sh eval [--holdout | --all]       score the answer keys
#   ./ingest.sh review list|show|approve|map|reject|apply|revert|log   schema gaps awaiting a reviewer
#   ./ingest.sh verify | embed | embeddings-setup | plan | convert | pack | purge
#
# Settings (all optional). Put them in ./.env.local, which this script loads:
#   GBRAIN_HOME=/path/to/brain       default ./brain   (a NEW folder; never your other brains)
#   DOCINGEST_WORK=/path/to/work     default $GBRAIN_HOME/work
#   LITELLM_BASE_URL=https://<proxy>/v1
#   LITELLM_API_KEY=<key>
#   GBRAIN_BIN=/path/to/gbrain       default: gbrain on PATH, then ~/.bun/bin/gbrain
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
if [ -f "$HERE/.env.local" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$HERE/.env.local"
  set +a
fi
export PATH="$HOME/.bun/bin:$PATH"
export GBRAIN_HOME="${GBRAIN_HOME:-$HERE/brain}"
[ -n "${DOCINGEST_WORK:-}" ] && export DOCINGEST_WORK
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
if [ -x "$HERE/.venv/bin/python" ]; then
  PY="$HERE/.venv/bin/python"
elif [ -x "$HERE/../.venv/bin/python" ]; then
  PY="$HERE/../.venv/bin/python"
else
  echo "No virtual environment found. Run ./setup.sh first." >&2
  exit 1
fi
exec "$PY" -m docingest "$@"
