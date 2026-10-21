#!/usr/bin/env bash
# Run the tests (needs ./setup.sh first). The embedding tests use a fake LiteLLM proxy,
# so no keys or network are needed.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.bun/bin:$PATH" PYTHONPATH="$PWD"
exec .venv/bin/python -m unittest discover -s tests -t . -v
