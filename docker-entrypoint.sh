#!/bin/sh
# Seed the corpus on first boot (idempotent — content-hash skips unchanged
# docs), then serve. With no data/raw cache mounted, this seeds the small
# bundled sample corpus; mount a populated data/raw/ (or exec the scrapers
# once) for the full 47-document corpus.
set -e

if [ ! -f "${EURAG_DATA_DIR:-/app/var}/registry.sqlite3" ]; then
  echo "eurag: seeding corpus…"
  python -m data.seed || echo "eurag: seed failed (continuing; ingest via API)"
fi

exec uvicorn api.main:app --host 0.0.0.0 --port 8000
