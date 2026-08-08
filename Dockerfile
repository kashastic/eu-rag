# EURAG self-host image. Multi-stage: build wheels once, ship a slim runtime.
FROM python:3.11-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip build
COPY pyproject.toml ./
COPY core ./core
COPY api ./api
COPY data ./data
COPY infra ./infra
COPY frontend ./frontend
RUN pip install --no-cache-dir --prefix=/install ".[prod]"

FROM python:3.11-slim
# non-root runtime user
RUN useradd --create-home --uid 10001 eurag
WORKDIR /app
COPY --from=build /install /usr/local
COPY --from=build /app /app
COPY docker-entrypoint.sh /usr/local/bin/eurag-entrypoint
RUN chmod +x /usr/local/bin/eurag-entrypoint && \
    mkdir -p /app/var /app/data/raw /home/eurag/.cache && \
    chown -R eurag:eurag /app /home/eurag/.cache
USER eurag

# writable state (registry, vectors, auth db) and corpus cache live here
VOLUME ["/app/var", "/app/data/raw"]
EXPOSE 8000
ENV EURAG_DATA_DIR=/app/var
# pin model caches under one path so a shared volume can hold the ~200 MB of
# ONNX downloads (embedder + reranker) once, instead of per replica
ENV HF_HOME=/home/eurag/.cache/hf \
    FASTEMBED_CACHE_PATH=/home/eurag/.cache/fastembed

# start-period covers first Pipeline init (ONNX model load; cold cache means
# a download) — seeding no longer happens in the API request path in prod
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"

ENTRYPOINT ["eurag-entrypoint"]
