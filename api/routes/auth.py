"""Auth endpoints: register, login, refresh. Present even when auth is
disabled (they just have no effect on unauthenticated routes), so a deploy
can turn EURAG_AUTH_ENABLED on without changing the client contract."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import current_principal
from core.security.auth import AuthError, AuthStore, Principal

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=10, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


def _store(request: Request) -> AuthStore:
    if not request.app.state.auth_enabled:
        raise HTTPException(status_code=404, detail="auth is disabled on this instance")
    return request.app.state.auth


@router.post("/auth/register")
def register(body: Credentials, request: Request):
    try:
        principal = _store(request).register(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"username": principal.username, "role": principal.role}


@router.post("/auth/login")
def login(body: Credentials, request: Request):
    store = _store(request)
    try:
        principal = store.authenticate(body.username, body.password)
    except AuthError:
        raise HTTPException(status_code=401, detail="invalid credentials") from None
    return store.issue_tokens(principal)


@router.post("/auth/refresh")
def refresh(body: RefreshRequest, request: Request):
    try:
        return _store(request).refresh(body.refresh_token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None


@router.get("/auth/me")
def me(request: Request, principal: Principal = Depends(current_principal)):
    return {
        "username": principal.username,
        "role": principal.role,
        "tenant": principal.tenant,
        "auth_enabled": request.app.state.auth_enabled,
    }
