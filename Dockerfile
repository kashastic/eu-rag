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
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.11-slim
# non-root runtime user
RUN useradd --create-home --uid 10001 eurag
WORKDIR /app
COPY --from=build /install /usr/local
COPY --from=build /app /app
COPY docker-entrypoint.sh /usr/local/bin/eurag-entrypoint
RUN chmod +x /usr/local/bin/eurag-entrypoint && \
    mkdir -p /app/var /app/data/raw && chown -R eurag:eurag /app
USER eurag

# writable state (registry, vectors, auth db) and corpus cache live here
VOLUME ["/app/var", "/app/data/raw"]
EXPOSE 8000
ENV EURAG_DATA_DIR=/app/var

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"

ENTRYPOINT ["eurag-entrypoint"]
