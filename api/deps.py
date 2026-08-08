"""Request-scoped security dependencies.

`current_principal` is the single place a request is turned into an identity:
- auth disabled (local default) → a built-in admin over the public corpus,
  no token required, behaviour identical to pre-M3.
- auth enabled → a valid Bearer access token is required; anything else is
  401. `allowed_tenants` then derives what that identity may read, and every
  route funnels through it — tenant scoping is defined once, here.
"""

from fastapi import Depends, Header, HTTPException, Request

from core.registry import PUBLIC_TENANT
from core.security.auth import AuthError, AuthStore, LOCAL_PRINCIPAL, Principal


def current_principal(
    request: Request, authorization: str | None = Header(default=None)
) -> Principal:
    if not request.app.state.auth_enabled:
        return LOCAL_PRINCIPAL
    store: AuthStore = request.app.state.auth
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return store.verify_access(authorization.split(" ", 1)[1].strip())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None


def optional_principal(
    request: Request, authorization: str | None = Header(default=None)
) -> Principal | None:
    """Like current_principal but returns None for an anonymous request
    instead of 401. A *present but invalid* token is still rejected — only a
    missing token means anonymous."""
    if not request.app.state.auth_enabled:
        return LOCAL_PRINCIPAL
    if not authorization:
        return None
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="malformed authorization header")
    try:
        return request.app.state.auth.verify_access(authorization.split(" ", 1)[1].strip())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None


def peer_ip(request: Request, trust_proxy: bool) -> str:
    """Best-effort client IP, the identity behind the anonymous quota and the
    rate limiter.

    X-Forwarded-For is only believed when `trust_proxy` says the app sits
    behind a proxy that rewrites it (our Caddy does): the header is
    client-settable, so trusting it on a directly reachable deployment would
    hand anyone an unlimited supply of fresh quota keys. Off, we key on the
    peer address — correct when direct, and merely coarse behind a proxy.

    Takes the flag as an argument (rather than reading app.state) so the rate
    limiter, which runs as plain Starlette middleware, can share it.
    """
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def client_ip(request: Request) -> str:
    """`peer_ip` with the running app's proxy-trust setting."""
    return peer_ip(request, request.app.state.settings.trust_proxy)


def paid_tier(request: Request, principal: Principal) -> dict:
    """Model plan for a logged-in user: BYOK → full cascade on their key;
    otherwise the free tier → cheap model, no escalation. In local (auth-off)
    single-user mode there is no tiering — the pipeline defaults (full
    cascade) are used."""
    if not request.app.state.auth_enabled:
        return {"tier": "local", "answer_model": None, "escalation_model": None, "api_key": None}
    settings = request.app.state.settings
    enc = request.app.state.auth.get_byok(principal.username) if request.app.state.auth else None
    cipher = request.app.state.cipher
    if enc and cipher:
        try:
            return {
                "tier": "byok",
                "answer_model": settings.llm_model,
                "escalation_model": settings.escalation_model,
                "api_key": cipher.decrypt(enc),
            }
        except Exception:
            pass  # corrupt/undecryptable key → fall through to free
    return {
        "tier": "free",
        "answer_model": settings.free_model,
        "escalation_model": "none",
        "api_key": None,
    }


def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return principal


def allowed_tenants(request: Request, principal: Principal) -> list[str] | None:
    """Tenants this identity may read. None = unscoped (local single-user)."""
    if not request.app.state.auth_enabled:
        return None
    if principal.tenant == PUBLIC_TENANT:
        return [PUBLIC_TENANT]
    return [principal.tenant, PUBLIC_TENANT]


def ingest_tenant(request: Request, principal: Principal) -> str:
    """Where this identity's uploads land. Local mode and the public tenant
    write to the shared corpus; a normal user writes to their own tenant."""
    if not request.app.state.auth_enabled:
        return PUBLIC_TENANT
    return principal.tenant
