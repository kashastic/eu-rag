from fastapi import APIRouter, Request

router = APIRouter(tags=["documents"])


@router.get("/documents")
def list_documents(request: Request):
    return {"documents": request.app.state.pipeline.registry.list_documents()}
