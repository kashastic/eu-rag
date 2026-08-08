from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import current_principal, ingest_tenant
from core.ingestion.document_loader import ProvenanceError, make_document
from core.security.auth import Principal
from core.security.pii import PIIError

router = APIRouter(tags=["ingest"])


# Upper bounds on an authenticated upload. Fixed constants, not config: they
# exist to stop a single request from pinning CPU in the chunker/embedder (and
# filling the registry), not to express a product limit. 500k chars is ~4x the
# largest act in the corpus (consolidated GDPR), so real documents fit.
class IngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=500_000)
    source_url: str = Field(default="", max_length=2000)
    source_type: str = Field(default="upload", max_length=40)
    language: str = Field(default="en", max_length=16)


@router.post("/ingest")
def ingest(
    body: IngestRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    try:
        doc = make_document(
            title=body.title,
            text=body.text,
            source_url=body.source_url,
            source_type=body.source_type,
            language=body.language,
        )
    except ProvenanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    tenant = ingest_tenant(request, principal)
    try:
        n_chunks = request.app.state.pipeline.ingest(doc, tenant=tenant)
    except PIIError as exc:
        # personal data never enters the corpus; tell the uploader what to fix
        if request.app.state.auth_enabled:
            request.app.state.auth.audit(
                principal.username, "ingest.pii_rejected", resource=doc.doc_id
            )
        raise HTTPException(status_code=422, detail=str(exc)) from None

    if request.app.state.auth_enabled:
        request.app.state.auth.audit(
            principal.username, "ingest", resource=doc.doc_id, detail=tenant
        )
    return {"doc_id": doc.doc_id, "chunks": n_chunks, "skipped": n_chunks == 0}
