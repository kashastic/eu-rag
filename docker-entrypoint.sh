#!/bin/sh
# Seed the corpus on first boot (idempotent — content-hash skips unchanged
# docs), then serve. With no data/raw cache mounted, this seeds the small
# bundled sample corpus; mount a populated data/raw/ (or run the prod
# compose, whose one-shot `seeder` service scrapes + seeds before the API
# starts) for the full 47-document corpus.
#
# EURAG_STRICT_BOOT=true makes a failed seed fatal (prod); otherwise the
# container still boots and serves whatever is ingested via the API (dev).
set -e

if [ ! -f "${EURAG_DATA_DIR:-/app/var}/registry.sqlite3" ]; then
  echo "eurag: seeding corpus…"
  case "${EURAG_STRICT_BOOT:-}" in
    1|true|TRUE|yes|YES)
      python -m data.seed
      ;;
    *)
      python -m data.seed || echo "eurag: seed failed (continuing; ingest via API)"
      ;;
  esac
fi

exec uvicorn api.main:app --host 0.0.0.0 --port 8000
