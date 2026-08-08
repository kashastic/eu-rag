"""Token-bucket rate limiter — deterministic, on a standalone app."""

import time

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from api.middleware.ratelimit import RateLimiter


def _app(rate_per_min, burst, trust_proxy=False):
    async def query(request):
        return PlainTextResponse("ok")

    async def docs(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/query", query, methods=["POST"]),
                            Route("/documents", docs)])
    app.add_middleware(RateLimiter, rate_per_min=rate_per_min, burst=burst,
                       trust_proxy=trust_proxy)
    return TestClient(app)


def test_burst_then_block():
    client = _app(rate_per_min=60, burst=3)
    codes = [client.post("/query").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429 and codes[4] == 429


def test_429_carries_retry_after():
    client = _app(rate_per_min=60, burst=1)
    client.post("/query")
    blocked = client.post("/query")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_unmetered_paths_are_never_limited():
    client = _app(rate_per_min=60, burst=1)
    client.post("/query")  # drains the bucket
    for _ in range(5):
        assert client.get("/documents").status_code == 200


def test_bucket_refills_over_time():
    client = _app(rate_per_min=6000, burst=1)  # 100 tokens/sec
    assert client.post("/query").status_code == 200
    assert client.post("/query").status_code == 429
    time.sleep(0.05)  # ~5 tokens refilled
    assert client.post("/query").status_code == 200


def test_forwarded_for_is_ignored_unless_trusted():
    """Default deployment: the header is client-settable, so honouring it would
    let one caller mint an unlimited supply of fresh buckets."""
    client = _app(rate_per_min=60, burst=1)
    assert client.post("/query", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.post("/query", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_trusted_proxy_keys_on_the_first_forwarded_hop():
    """Behind Caddy every request has the same peer address; without this the
    whole internet would share one bucket."""
    client = _app(rate_per_min=60, burst=1, trust_proxy=True)
    a = {"X-Forwarded-For": "1.1.1.1"}
    assert client.post("/query", headers=a).status_code == 200
    assert client.post("/query", headers=a).status_code == 429
    # a different real client, same proxy — unaffected
    assert client.post("/query", headers={"X-Forwarded-For": "2.2.2.2, 10.0.0.1"}).status_code == 200


def test_distinct_bearer_tokens_get_separate_buckets():
    client = _app(rate_per_min=60, burst=1)
    a = {"Authorization": "Bearer alice-token"}
    b = {"Authorization": "Bearer bob-token"}
    assert client.post("/query", headers=a).status_code == 200
    assert client.post("/query", headers=a).status_code == 429
    # bob is unaffected by alice exhausting her bucket
    assert client.post("/query", headers=b).status_code == 200
