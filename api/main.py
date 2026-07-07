"""FastAPI app: query + ingest endpoints, auth, admin, and the static chat UI."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.middleware.headers import SecurityHeaders
from api.middleware.ratelimit import RateLimiter
from api.routes.admin import router as admin_router
from api.routes.auth import router as auth_router
from api.routes.conversations import router as conversations_router
from api.routes.documents import router as documents_router
from api.routes.ingest import router as ingest_router
from api.routes.query import router as query_router
from core.config import get_settings
from core.conversations import ConversationStore
from core.db import Database, database_url
from core.pipeline import Pipeline
from core.security.auth import AuthStore, load_or_create_secret

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static"
# read once at import: middleware is attached to the app before any request,
# so its config can't come from the per-request lifespan. Runtime toggles for
# rate limiting therefore require a process restart (documented in .env).
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # read fresh (not the import-time singleton) so runtime env is honoured
    settings = get_settings()
    app.state.pipeline = Pipeline(settings)
    app.state.auth_enabled = settings.auth_enabled
    app.state.auth = None
    app.state.conversations = None
    app.state.db = None
    if settings.auth_enabled:
        url = database_url()
        db = Database(url, sqlite_path=settings.data_dir / "eurag.sqlite3")
        secret = load_or_create_secret(settings.jwt_secret_path, settings.jwt_secret)
        app.state.db = db
        app.state.auth = AuthStore(db, secret)
        app.state.conversations = ConversationStore(db)
        backend = "postgres" if db.is_pg else "sqlite"
        logging.getLogger(__name__).info(
            "auth enabled (%s store) — JWT required, chat history on", backend
        )
    yield
    app.state.pipeline.close()
    if app.state.db is not None:
        app.state.db.close()


app = FastAPI(title="EURAG — EU SME Intelligence Hub", lifespan=lifespan)
if _settings.cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
app.add_middleware(SecurityHeaders)
if _settings.rate_limit_per_min > 0:
    _redis = None
    if _settings.redis_url:
        import redis as _redis_lib

        _redis = _redis_lib.from_url(_settings.redis_url, decode_responses=True)
    app.add_middleware(
        RateLimiter,
        rate_per_min=_settings.rate_limit_per_min,
        burst=_settings.rate_limit_burst,
        redis_client=_redis,
    )
app.include_router(query_router)
app.include_router(ingest_router)
app.include_router(documents_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(conversations_router)


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
