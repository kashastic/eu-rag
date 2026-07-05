"""End-to-end API test: seed → query → cited answer, no network, no LLM key."""

from fastapi.testclient import TestClient

from data.seed import seed


def test_full_flow(settings):
    from api.main import app

    with TestClient(app) as client:
        n_docs = len(seed(app.state.pipeline))

        health = client.get("/healthz").json()
        assert health["status"] == "ok"
        assert health["documents"] == n_docs >= 4

        docs = client.get("/documents").json()["documents"]
        assert {d["source_type"] for d in docs} >= {"eur-lex", "ec-portal"}

        res = client.post(
            "/query",
            json={"question": "When must a personal data breach be notified?"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["mode"] in ("extractive", "llm")
        assert body["citations"], "answers must carry citations"
        for citation in body["citations"]:
            assert citation["chunk_id"]
            assert citation["title"]


def test_ingest_endpoint_validates_provenance(settings):
    from api.main import app

    with TestClient(app) as client:
        res = client.post(
            "/ingest",
            json={"title": "   ", "text": "some text"},
        )
        assert res.status_code in (422,)


def test_ingest_then_query_roundtrip(settings):
    from api.main import app

    with TestClient(app) as client:
        res = client.post(
            "/ingest",
            json={
                "title": "Fictional Widget Directive",
                "text": "The Fictional Widget Directive 9999/42 requires all widgets "
                "to be registered in the Widget Register before sale. " * 5,
                "source_url": "https://example.eu/widgets",
                "source_type": "upload",
            },
        )
        assert res.status_code == 200
        assert res.json()["chunks"] >= 1

        body = client.post(
            "/query", json={"question": "What does Directive 9999/42 require?"}
        ).json()
        assert any("Widget" in c["title"] for c in body["citations"])


def test_query_rejects_trivial_input(settings):
    from api.main import app

    with TestClient(app) as client:
        assert client.post("/query", json={"question": "ab"}).status_code == 422


def test_query_accepts_optional_industry(settings):
    from api.main import app

    with TestClient(app) as client:
        seed(app.state.pipeline)
        res = client.post(
            "/query",
            json={"question": "When must a data breach be notified?", "industry": "software"},
        )
        assert res.status_code == 200
        assert client.post(
            "/query", json={"question": "abc", "industry": "x" * 81}
        ).status_code == 422
