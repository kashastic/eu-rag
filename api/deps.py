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
