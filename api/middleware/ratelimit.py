"""In-process token-bucket rate limiter.

Protects the expensive, abusable routes (/query calls the LLM and can trigger
an Opus escalation; /ingest embeds) from a single client draining the API
budget. Keyed by authenticated username when a valid-looking bearer token is
present, else by client IP — so one logged-in user can't spend another's
budget and an anonymous flood is capped per source.

In-process is honest for a single-instance deployment; a multi-instance
deployment behind a load balancer should move this to Redis (the interface is
one `allow()` call, so that swap is local). Disabled when rate == 0.
"""

import time
from collections import OrderedDict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# only the routes that cost money / compute; reads and auth are unmetered
_LIMITED_PATHS = ("/query", "/ingest")
_MAX_BUCKETS = 10_000  # cap memory; oldest keys evicted


class TokenBucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, capacity: float):
        self.tokens = capacity
        self.updated = time.monotonic()


class RateLimiter(BaseHTTPMiddleware):
    def __init__(self, app, rate_per_min: int, burst: int):
        super().__init__(app)
        self.rate = rate_per_min / 60.0  # tokens per second
        self.capacity = float(max(burst, 1))
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()

    def _key(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            # coarse identity — the token string itself, no verification here;
            # a forged token still gets rate-limited as its own bucket
            return "tok:" + auth.split(" ", 1)[1][:32]
        client = request.client
        return "ip:" + (client.host if client else "unknown")

    def _allow(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= _MAX_BUCKETS:
                self._buckets.popitem(last=False)
            bucket = TokenBucket(self.capacity)
            self._buckets[key] = bucket
        else:
            bucket.tokens = min(
                self.capacity, bucket.tokens + (now - bucket.updated) * self.rate
            )
            bucket.updated = now
            self._buckets.move_to_end(key)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True, 0.0
        retry_after = (1.0 - bucket.tokens) / self.rate if self.rate else 60.0
        return False, retry_after

    async def dispatch(self, request: Request, call_next):
        if request.url.path not in _LIMITED_PATHS:
            return await call_next(request)
        allowed, retry_after = self._allow(self._key(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded — slow down"},
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )
        return await call_next(request)
