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


@router.get("/account")
def account(request: Request, p: Principal = Depends(current_principal)):
    has_key = bool(request.app.state.auth and request.app.state.auth.get_byok(p.username))
    return {
        "username": p.username,
        "role": p.role,
        "tier": "byok" if has_key else "free",
        "has_api_key": has_key,
        "byok_available": request.app.state.cipher is not None,
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
