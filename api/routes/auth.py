"""Auth endpoints: register, login, refresh. Present even when auth is
disabled (they just have no effect on unauthenticated routes), so a deploy
can turn EURAG_AUTH_ENABLED on without changing the client contract."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import client_ip, current_principal
from core.security import google_oauth, turnstile
from core.security.auth import AuthError, AuthStore, Principal

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=10, max_length=200)


class RegisterRequest(Credentials):
    # bot gate: automated signups would bypass the anonymous quota and get
    # server-key answers. Checked only when a Turnstile secret is configured.
    turnstile_token: str | None = Field(default=None, max_length=2048)


class RefreshRequest(BaseModel):
    refresh_token: str


def _store(request: Request) -> AuthStore:
    if not request.app.state.auth_enabled:
        raise HTTPException(status_code=404, detail="auth is disabled on this instance")
    return request.app.state.auth


@router.post("/auth/register")
def register(body: RegisterRequest, request: Request):
    store = _store(request)
    settings = request.app.state.settings
    if settings.turnstile_secret and not turnstile.verify(
        body.turnstile_token, settings.turnstile_secret, remoteip=client_ip(request)
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "turnstile_failed",
                "message": "Verification failed — please retry the challenge.",
            },
        )
    try:
        principal = store.register(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"username": principal.username, "role": principal.role}


class GoogleCredential(BaseModel):
    # the ID token Google Identity Services hands the browser
    credential: str = Field(min_length=20, max_length=8192)


@router.post("/auth/google")
def google_login(body: GoogleCredential, request: Request):
    """Sign in (or sign up) with a Google ID token.

    No Turnstile here on purpose: Google has already done the bot work, and the
    token is bound to our client id, so this route cannot be driven by a script
    that hasn't been through a real Google login.
    """
    store = _store(request)
    client_id = request.app.state.settings.google_client_id
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "google_disabled",
                "message": "Google sign-in isn't configured on this server.",
            },
        )
    try:
        claims = google_oauth.verify_id_token(body.credential, client_id)
    except google_oauth.GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    principal = store.upsert_google_user(
        claims["sub"], claims.get("email", ""), claims.get("name")
    )
    return store.issue_tokens(principal)


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
