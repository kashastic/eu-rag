"""FastAPI app: query + ingest endpoints and the static chat UI."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes.documents import router as documents_router
from api.routes.ingest import router as ingest_router
from api.routes.query import router as query_router
from core.pipeline import Pipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = Pipeline()
    yield
    app.state.pipeline.close()


app = FastAPI(title="EURAG — EU SME Intelligence Hub", lifespan=lifespan)
app.include_router(query_router)
app.include_router(ingest_router)
app.include_router(documents_router)


@app.get("/healthz")
def healthz():
    pipeline: Pipeline = app.state.pipeline
    return {
        "status": "ok",
        "embedder": pipeline.embedder.name,
        "llm": pipeline.llm.name,
        "documents": len(pipeline.registry.list_documents()),
    }


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(_STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
