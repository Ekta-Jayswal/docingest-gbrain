# docingest + gbrain in one image.
#
# Build:  docker build -t docingest .
# Run:    docker run --rm \
#           -v "$PWD/inputs:/input:ro" \
#           -v docingest-brain:/data/brain \
#           -v docingest-work:/data/work \
#           docingest ingest /input
# AI extraction: add  -e LITELLM_BASE_URL -e LITELLM_API_KEY  and  ingest /input --llm
#
# The container exits non-zero if any document fails to convert or any
# verification check fails, so it can gate a CI job or a batch scheduler.

ARG BUN_VERSION=1.4.3
FROM oven/bun:${BUN_VERSION}-slim AS bun

FROM python:3.12-slim

ARG GBRAIN_REF=v0.60.281.0

# git: fetch gbrain. libreoffice-writer: legacy .doc conversion.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates libreoffice-writer-nogui \
 && rm -rf /var/lib/apt/lists/*

COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun

# gbrain, pinned to the release this pipeline was tested against.
RUN git clone --depth 1 --branch "${GBRAIN_REF}" https://github.com/garrytan/gbrain.git /opt/gbrain \
 && cd /opt/gbrain \
 && GBRAIN_NO_AUTOPILOT_INSTALL=1 bun install --frozen-lockfile \
 && printf '#!/bin/sh\nexec bun /opt/gbrain/src/cli.ts "$@"\n' > /usr/local/bin/gbrain \
 && chmod +x /usr/local/bin/gbrain \
 && gbrain --version

WORKDIR /app
# requirements.lock pins every transitive dependency (see requirements.txt for the direct ones).
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY docingest ./docingest
COPY answer_keys ./answer_keys
COPY tests ./tests
COPY sample_documents ./sample_documents
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data/brain /data/work /input \
 && chown -R app /data /opt/gbrain
USER app

ENV GBRAIN_HOME=/data/brain \
    DOCINGEST_WORK=/data/work \
    GBRAIN_NO_AUTOPILOT_INSTALL=1 \
    GBRAIN_NO_REEMBED=1 \
    PYTHONUNBUFFERED=1

VOLUME ["/data/brain", "/data/work"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["ingest", "/input"]
