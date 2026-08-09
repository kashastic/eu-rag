"""Request-scoped security dependencies.

`current_principal` is the single place a request is turned into an identity:
- auth disabled (local default) → a built-in admin over the public corpus,
  no token required, behaviour identical to pre-M3.
- auth enabled → a valid Bearer access token is required; anything else is
  401. `allowed_tenants` then derives what that identity may read, and every
  route funnels through it — tenant scoping is defined once, here.

A valid signature is necessary but not sufficient: the account must also still
exist, so that erasing one ends its sessions immediately rather than 15 minutes
later (`_still_exists`).
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
        principal = store.verify_access(authorization.split(" ", 1)[1].strip())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    return _still_exists(store, principal)


def _still_exists(store: AuthStore, principal: Principal) -> Principal:
    """Reject a token whose account has been erased.

    Access tokens are stateless by design and live for 15 minutes, so without
    this an account deleted under DELETE /account keeps a working session for
    up to a quarter of an hour: it could still ask questions (on a quota row
    that erasure reset), and the UI would still show it as signed in. "Deleted
    immediately" has to mean immediately, so an authenticated request pays one
    primary-key lookup. Anonymous requests never reach here, and the ask path
    was already doing a lookup of its own (`paid_tier` → `get_byok`).
    """
    if not store.user_exists(principal.username):
        raise HTTPException(status_code=401, detail="account no longer exists")
    return principal


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
    store: AuthStore = request.app.state.auth
    try:
        principal = store.verify_access(authorization.split(" ", 1)[1].strip())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    return _still_exists(store, principal)


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


def spend_free_question(request: Request, principal: Principal, plan: dict) -> int | None:
    """Take one from a free user's lifetime allowance, or refuse.

    Returns the remaining count for the response, or None when no gate applies
    (BYOK — billed to the user, so the server has no reason to count it — and
    local auth-off mode). Raises **402** when the allowance is spent: never 401,
    because the web client treats 401 as "refresh the session token" and would
    loop instead of showing the wall (same trap as `byok_key_rejected`, see
    docs/SECURITY.md).

    Lives here rather than in a route because BOTH logged-in ask paths must
    enforce it identically — `/query` with a token and
    `/conversations/{id}/messages` — and a gate present on one of two doors is
    not a gate.
    """
    if plan["tier"] != "free":
        return None
    quota = request.app.state.user_quota
    if quota is None:
        return None
    limit = request.app.state.settings.free_user_questions
    allowed, remaining = quota.consume(principal.username, limit)
    if not allowed:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "free_limit_reached",
                "message": (
                    f"You've used your {limit} free questions. Add your own "
                    "Anthropic API key in Settings to keep going — those "
                    "answers are billed to you, and use the stronger models."
                ),
            },
        )
    return remaining


def refund_free_question(request: Request, principal: Principal, plan: dict) -> None:
    """Give back a question whose answer never arrived (see the anon path's
    refund — same parallel-overrun safety, same reason)."""
    if plan["tier"] == "free" and request.app.state.user_quota is not None:
        request.app.state.user_quota.refund(principal.username)


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
