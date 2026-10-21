#!/usr/bin/env bash
# Run docingest locally with the project's virtualenv.
#   ./ingest.sh ingest <file-or-folder> [--llm | --llm-all] [--kind brd]
#   ./ingest.sh plan <file-or-folder>
#   ./ingest.sh verify [document-key ...]
#   ./ingest.sh convert <file>
#   ./ingest.sh pack | purge <slug-prefix> [--yes]
# The brain defaults to ../brd-brain (override with GBRAIN_HOME); work files go to ./work.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$HERE")"
export PATH="$HOME/.bun/bin:$PATH"
export GBRAIN_HOME="${GBRAIN_HOME:-$PROJECT/brd-brain}"
export DOCINGEST_WORK="${DOCINGEST_WORK:-$HERE/work}"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$PROJECT/.venv/bin/python" -m docingest "$@"
