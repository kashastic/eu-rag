from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import allowed_tenants, current_principal
from core.security.auth import Principal, question_hash

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    # optional sector context: tailors the answer wording; never used for
    # retrieval. Also logged as research input for corpus expansion.
    industry: str | None = Field(default=None, max_length=80)


@router.post("/query")
def query(
    body: QueryRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    tenants = allowed_tenants(request, principal)
    result = request.app.state.pipeline.query(
        body.question, industry=body.industry, tenants=tenants
    )
    if request.app.state.auth_enabled:
        # log the hash, never the raw question — queries can contain PII
        request.app.state.auth.audit(
            principal.username, "query", detail=question_hash(body.question)
        )
    return result.to_dict()
