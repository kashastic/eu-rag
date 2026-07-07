"""Token-bucket rate limiter for the expensive routes (/query calls the LLM
and can escalate to Opus; /ingest embeds).

Two backends, chosen by whether a Redis client is supplied:
- **Redis** (production, multi-instance): the bucket lives in Redis so a
  client's limit is shared across every app instance behind the load
  balancer. Refill + take is one atomic Lua script.
- **In-process** (single instance / local): a per-process dict of buckets.

Keyed by bearer token when present else client IP, so one user can't spend
another's budget. Keys are hashed before hitting Redis (never store raw
tokens). Disabled when rate == 0.
"""

import hashlib
import time
from collections import OrderedDict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_LIMITED_PATHS = ("/query", "/ingest")
_MAX_BUCKETS = 10_000

# atomic refill-and-take; KEYS[1]=bucket, ARGV=rate, capacity, now, ttl
_REDIS_LUA = """
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1])
local ts = tonumber(b[2])
local rate = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
if tokens == nil then tokens = cap; ts = now end
tokens = math.min(cap, tokens + (now - ts) * rate)
local allowed = 0
if tokens >= 1 then tokens = tokens - 1; allowed = 1 end
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
return {allowed, tostring(tokens)}
"""


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, capacity: float):
        self.tokens = capacity
        self.updated = time.monotonic()


class RateLimiter(BaseHTTPMiddleware):
    def __init__(self, app, rate_per_min: int, burst: int, redis_client=None):
        super().__init__(app)
        self.rate = rate_per_min / 60.0  # tokens/sec
        self.capacity = float(max(burst, 1))
        self._redis = redis_client
        self._sha = None
        if redis_client is not None:
            self._sha = redis_client.script_load(_REDIS_LUA)
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def _key(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            raw = "tok:" + auth.split(" ", 1)[1]
        else:
            client = request.client
            raw = "ip:" + (client.host if client else "unknown")
        return "eurag:rl:" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _allow_redis(self, key: str) -> tuple[bool, float]:
        allowed, tokens = self._redis.evalsha(
            self._sha, 1, key, self.rate, self.capacity, time.time(), 3600
        )
        tokens = float(tokens)
        if int(allowed) == 1:
            return True, 0.0
        return False, (1.0 - tokens) / self.rate if self.rate else 60.0

    def _allow_local(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= _MAX_BUCKETS:
                self._buckets.popitem(last=False)
            bucket = _Bucket(self.capacity)
            self._buckets[key] = bucket
        else:
            bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
            bucket.updated = now
            self._buckets.move_to_end(key)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True, 0.0
        return False, (1.0 - bucket.tokens) / self.rate if self.rate else 60.0

    async def dispatch(self, request: Request, call_next):
        if request.url.path not in _LIMITED_PATHS:
            return await call_next(request)
        key = self._key(request)
        try:
            allowed, retry_after = (
                self._allow_redis(key) if self._redis is not None else self._allow_local(key)
            )
        except Exception:
            # never let a limiter outage take down the API — fail open
            allowed, retry_after = True, 0.0
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded — slow down"},
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )
        return await call_next(request)
