"""Account + BYOK. A logged-in user can store their own Anthropic key to
unlock the full model cascade on their own bill. The key is encrypted at rest
(AES-256-GCM) and never returned or logged — status shows only whether one is
set. BYOK requires the server to have EURAG_ENCRYPTION_KEY configured."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import current_principal
from core.security.auth import Principal

router = APIRouter(tags=["account"])


class ApiKeyBody(BaseModel):
    api_key: str = Field(min_length=20, max_length=400)


class DeleteAccountBody(BaseModel):
    """Typed confirmation. Not a CSRF defence — the session is a bearer token in
    localStorage, so there is nothing for a cross-site form to ride — but an
    irreversible, unrecoverable action should take more than one click, and
    asking for the *username* works for Google accounts too (they have no
    password to re-enter)."""

    confirm_username: str


@router.get("/account")
def account(request: Request, p: Principal = Depends(current_principal)):
    auth = request.app.state.auth
    has_key = bool(auth and auth.get_byok(p.username))
    quota = request.app.state.user_quota
    limit = request.app.state.settings.free_user_questions
    return {
        "username": p.username,
        "role": p.role,
        "tier": "byok" if has_key else "free",
        "has_api_key": has_key,
        "byok_available": request.app.state.cipher is not None,
        # free-tier allowance, so the UI can show it before the wall is hit.
        # Read-only here — /query and the chat route are what actually spend it.
        "free_limit": limit,
        "free_remaining": quota.remaining(p.username, limit) if quota else None,
        # when the stored key was set, so the UI can nudge a rotation. Never
        # the key itself.
        "api_key_set_at": auth.byok_set_at(p.username) if (auth and has_key) else None,
    }


@router.put("/account/api-key")
def set_key(body: ApiKeyBody, request: Request, p: Principal = Depends(current_principal)):
    cipher = request.app.state.cipher
    if cipher is None:
        raise HTTPException(
            status_code=503,
            detail="key storage unavailable — server has no EURAG_ENCRYPTION_KEY set",
        )
    key = body.api_key.strip()
    if not key.startswith("sk-ant-"):
        raise HTTPException(status_code=422, detail="that doesn't look like an Anthropic API key")
    request.app.state.auth.set_byok(p.username, cipher.encrypt(key))
    request.app.state.auth.audit(p.username, "account.byok_set")
    return {"tier": "byok", "has_api_key": True}


@router.delete("/account/api-key")
def clear_key(request: Request, p: Principal = Depends(current_principal)):
    request.app.state.auth.clear_byok(p.username)
    request.app.state.auth.audit(p.username, "account.byok_cleared")
    return {"tier": "free", "has_api_key": False}


@router.delete("/account")
def delete_account(
    body: DeleteAccountBody,
    request: Request,
    p: Principal = Depends(current_principal),
):
    """Erase the account and everything attached to it (GDPR Art. 17).

    Deletion order is deliberate — content first, account row last — so that a
    failure part-way through leaves a usable account with some data missing,
    never a login whose data has vanished. The user's tenant *is* their
    username (see `AuthStore.register`), which is what makes one call able to
    reach their uploaded documents.

    What survives, on purpose: the audit trail, pseudonymised to
    `deleted_account` (see `AuthStore.erase_user`), and anything already sent
    to Anthropic to answer a question, which we cannot reach. Both are stated
    in /privacy.
    """
    if request.app.state.auth is None:
        raise HTTPException(status_code=404, detail="auth is disabled on this instance")
    if body.confirm_username.strip().lower() != p.username:
        raise HTTPException(
            status_code=422,
            detail="type your username exactly to confirm deletion",
        )

    state = request.app.state
    chats = state.conversations.erase_user(p.username) if state.conversations else 0
    # `erase_tenant` refuses "public" outright; skip it rather than 500 so an
    # account that landed on the public tenant can still be deleted.
    docs = 0 if p.tenant == "public" else state.pipeline.erase_tenant(p.tenant)
    if state.user_quota is not None:
        state.user_quota.erase_user(p.username)
    state.auth.erase_user(p.username)  # last: revokes sessions, drops the row

    return {"deleted": True, "conversations_erased": chats, "documents_erased": docs}
