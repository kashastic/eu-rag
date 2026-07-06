"""FastAPI app: query + ingest endpoints, auth, admin, and the static chat UI."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes.admin import router as admin_router
from api.routes.auth import router as auth_router
from api.routes.documents import router as documents_router
from api.routes.ingest import router as ingest_router
from api.routes.query import router as query_router
from core.config import get_settings
from core.pipeline import Pipeline
from core.security.auth import AuthStore, load_or_create_secret

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.pipeline = Pipeline(settings)
    app.state.auth_enabled = settings.auth_enabled
    app.state.auth = None
    if settings.auth_enabled:
        secret = load_or_create_secret(settings.jwt_secret_path, settings.jwt_secret)
        app.state.auth = AuthStore(settings.auth_path, secret)
        logging.getLogger(__name__).info("auth enabled — JWT required on all routes")
    yield
    app.state.pipeline.close()
    if app.state.auth is not None:
        app.state.auth.close()


app = FastAPI(title="EURAG — EU SME Intelligence Hub", lifespan=lifespan)
app.include_router(query_router)
app.include_router(ingest_router)
app.include_router(documents_router)
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/healthz")
def healthz():
    pipeline: Pipeline = app.state.pipeline
    return {
        "status": "ok",
        "embedder": pipeline.embedder.name,
        "llm": pipeline.llm.name,
        "documents": len(pipeline.registry.list_documents()),
        "auth_enabled": app.state.auth_enabled,
        "encryption": pipeline.registry._cipher is not None,
    }


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(_STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
