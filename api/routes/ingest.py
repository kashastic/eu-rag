from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.ingestion.document_loader import ProvenanceError, make_document

router = APIRouter(tags=["ingest"])


class IngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1)
    source_url: str = ""
    source_type: str = "upload"
    language: str = "en"


@router.post("/ingest")
def ingest(body: IngestRequest, request: Request):
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
    n_chunks = request.app.state.pipeline.ingest(doc)
    return {"doc_id": doc.doc_id, "chunks": n_chunks, "skipped": n_chunks == 0}
