from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    # optional sector context: tailors the answer wording; never used for
    # retrieval. Also logged as research input for corpus expansion.
    industry: str | None = Field(default=None, max_length=80)


@router.post("/query")
def query(body: QueryRequest, request: Request):
    result = request.app.state.pipeline.query(body.question, industry=body.industry)
    return result.to_dict()
