import os

import pytest

from core.config import Settings
from core.pipeline import Pipeline
from data.seed import seed

# Disable rate limiting for the whole session before api.main is imported.
# The middleware is configured once at import from module-level settings and
# its buckets persist across tests on the shared app object; leaving it on
# would make unrelated API tests flaky. The limiter itself is tested directly
# on a standalone app in tests/unit/test_ratelimit.py.
os.environ.setdefault("EURAG_RATE_LIMIT_PER_MIN", "0")


@pytest.fixture()
def settings(tmp_path, monkeypatch) -> Settings:
    """Offline settings: hashing embedder, no reranker, isolated data dir."""
    monkeypatch.setenv("EURAG_EMBEDDER", "hash")
    monkeypatch.setenv("EURAG_RERANKER", "none")
    monkeypatch.setenv("EURAG_DATA_DIR", str(tmp_path / "var"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return Settings()


@pytest.fixture()
def pipeline(settings):
    p = Pipeline(settings)
    yield p
    p.close()


@pytest.fixture()
def seeded_pipeline(pipeline):
    seed(pipeline)
    return pipeline
