"""Admin-only endpoints: the audit trail and account erasure."""

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import require_admin
from core.security.auth import Principal

router = APIRouter(tags=["admin"])


def _require_auth(request: Request) -> None:
    if not request.app.state.auth_enabled:
        raise HTTPException(status_code=404, detail="auth is disabled on this instance")


@router.get("/admin/audit")
def audit_log(
    request: Request,
    limit: int = 100,
    principal: Principal = Depends(require_admin),
):
    _require_auth(request)
    return {"entries": request.app.state.auth.audit_entries(min(limit, 1000))}


@router.delete("/admin/tenants/{tenant}")
def erase_tenant(
    tenant: str,
    request: Request,
    principal: Principal = Depends(require_admin),
):
    """Erase every document a user uploaded (account-deletion support)."""
    _require_auth(request)
    if tenant == "public":
        raise HTTPException(status_code=400, detail="refusing to erase the public corpus")
    n = request.app.state.pipeline.erase_tenant(tenant)
    request.app.state.auth.audit(
        principal.username, "tenant.erase", resource=tenant, detail=str(n)
    )
    return {"tenant": tenant, "documents_erased": n}
