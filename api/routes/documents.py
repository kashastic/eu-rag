from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import allowed_tenants, current_principal
from core.security.auth import Principal

router = APIRouter(tags=["documents"])


@router.get("/documents")
def list_documents(
    request: Request, principal: Principal = Depends(current_principal)
):
    tenants = allowed_tenants(request, principal)
    return {
        "documents": request.app.state.pipeline.registry.list_documents(tenants)
    }


@router.delete("/documents/{doc_id}")
def erase_document(
    doc_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    """GDPR Art. 17 erasure. A user may erase documents in their own tenant;
    an admin may erase any. Public official texts are erasable by admins only."""
    pipeline = request.app.state.pipeline
    owner = pipeline.registry.document_tenant(doc_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="document not found")
    if request.app.state.auth_enabled and not (
        principal.is_admin or owner == principal.tenant
    ):
        raise HTTPException(status_code=403, detail="not your document")

    pipeline.erase_document(doc_id)
    if request.app.state.auth_enabled:
        request.app.state.auth.audit(
            principal.username, "document.erase", resource=doc_id, detail=owner
        )
    return {"erased": doc_id}
