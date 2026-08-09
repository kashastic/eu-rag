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


def test_ingest_rejects_oversize_payload(settings):
    """Caps keep one request from pinning the chunker/embedder on megabytes."""
    from api.main import app

    with TestClient(app) as client:
        too_long = client.post(
            "/ingest", json={"title": "Huge", "text": "widget " * 80_000}
        )
        assert too_long.status_code == 422
        bad_url = client.post(
            "/ingest",
            json={"title": "Doc", "text": "body", "source_url": "https://e.eu/"
                  + "a" * 2000},
        )
        assert bad_url.status_code == 422


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


def test_query_accepts_history_and_passes_it_to_the_pipeline(settings, monkeypatch):
    """A follow-up carries no topic of its own, so the prior turns must reach
    the pipeline or retrieval runs on a fragment (observed in prod: "what if I
    have 29 people?" retrieved the Pay Transparency Directive). Local mode uses
    the extractive client and builds no contextualizer, so this asserts the
    plumbing rather than the rewrite — the rewrite itself is covered by the
    golden harness and tests/unit/test_expansion.py."""
    from api.main import app

    with TestClient(app) as client:
        seed(app.state.pipeline)
        seen: dict = {}
        original = app.state.pipeline.query

        def spy(question, *args, **kwargs):
            seen["history"] = kwargs.get("history")
            return original(question, *args, **kwargs)

        monkeypatch.setattr(app.state.pipeline, "query", spy)

        res = client.post(
            "/query",
            json={
                "question": "what if I have 29 people?",
                "history": [
                    {
                        "question": "Do I need a data protection officer?",
                        "answer": "It depends on your core activities.",
                    }
                ],
            },
        )

        assert res.status_code == 200
        assert seen["history"] == [
            ("Do I need a data protection officer?", "It depends on your core activities.")
        ]


def test_query_without_history_is_unchanged(settings, monkeypatch):
    """Every existing caller omits history; they must keep working and must
    hand the pipeline an empty list, never None-shaped surprises."""
    from api.main import app

    with TestClient(app) as client:
        seed(app.state.pipeline)
        seen: dict = {}
        original = app.state.pipeline.query

        def spy(question, *args, **kwargs):
            seen["history"] = kwargs.get("history")
            return original(question, *args, **kwargs)

        monkeypatch.setattr(app.state.pipeline, "query", spy)
        res = client.post("/query", json={"question": "What is an SME?"})

        assert res.status_code == 200
        assert seen["history"] == []


def test_query_rejects_oversize_history(settings):
    """History is client-supplied text that lands in a prompt, so it is capped
    the same way /ingest fields are."""
    from api.main import app

    with TestClient(app) as client:
        res = client.post(
            "/query",
            json={
                "question": "follow up?",
                "history": [{"question": "q", "answer": "x"}] * 11,
            },
        )
        assert res.status_code == 422

        res = client.post(
            "/query",
            json={
                "question": "follow up?",
                "history": [{"question": "q", "answer": "x" * 2001}],
            },
        )
        assert res.status_code == 422
