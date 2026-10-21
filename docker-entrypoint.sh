#!/bin/sh
# Initialise an empty brain on first start (keyless PGLite, embeddings off), then run docingest.
set -e
mkdir -p "$GBRAIN_HOME" "$DOCINGEST_WORK"
if [ ! -f "$GBRAIN_HOME/.gbrain/config.json" ]; then
  gbrain init --pglite --no-embedding --non-interactive >/dev/null
  echo "[entrypoint] initialised a new brain in $GBRAIN_HOME"
fi
exec python -m docingest "$@"
