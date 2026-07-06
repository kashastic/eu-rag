"""M3 security spine — adversarial isolation, encryption at rest, GDPR
erasure, and the authenticated API flow."""

import os

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from core.ingestion.document_loader import make_document
from core.pipeline import Pipeline


def _upload(text, tenant, pipeline, title="Private note"):
    doc = make_document(title=f"{title} {tenant}", text=text, source_type="upload")
    pipeline.ingest(doc, tenant=tenant)
    return doc.doc_id


# --------------------------------------------------------------------------
# Tenant isolation — the load-bearing guarantee, attacked three ways
# --------------------------------------------------------------------------

@pytest.fixture()
def two_tenant_pipeline(settings):
    p = Pipeline(settings)
    _upload("SECRETALPHA belongs to alice and mentions widgets and GDPR.", "alice", p)
    _upload("SECRETBETA belongs to bob and mentions widgets and GDPR.", "bob", p)
    yield p
    p.close()


def test_query_never_returns_another_tenants_text(two_tenant_pipeline):
    bob_view = ["bob", "public"]
    chunks = two_tenant_pipeline.registry.get_chunks(
        two_tenant_pipeline.retriever.retrieve("widgets GDPR", k=10, tenants=bob_view),
        bob_view,
    )
    texts = " ".join(c.text for c in chunks)
    assert "SECRETALPHA" not in texts  # alice's data must not leak to bob
    assert "SECRETBETA" in texts  # bob still sees his own


def test_get_chunks_drops_foreign_ids_even_when_asked_directly(two_tenant_pipeline):
    # adversary knows alice's chunk id and requests it under bob's scope
    reg = two_tenant_pipeline.registry
    alice_ids = [
        c.chunk_id for c in reg.all_chunks() if "SECRETALPHA" in c.text
    ]
    assert alice_ids
    leaked = reg.get_chunks(alice_ids, tenants=["bob", "public"])
    assert leaked == []  # the hard gate


def test_vector_search_is_tenant_filtered(two_tenant_pipeline):
    p = two_tenant_pipeline
    hits = p.vectors.search(p.embedder.embed_query("widgets GDPR"), k=10, tenants=["bob"])
    ids = {cid for cid, _ in hits}
    alice_ids = {c.chunk_id for c in p.registry.all_chunks() if "SECRETALPHA" in c.text}
    assert ids.isdisjoint(alice_ids)


def test_document_listing_is_tenant_scoped(two_tenant_pipeline):
    titles = [
        d["title"] for d in two_tenant_pipeline.registry.list_documents(["bob", "public"])
    ]
    assert any("bob" in t for t in titles)
    assert not any("alice" in t for t in titles)


# --------------------------------------------------------------------------
# Encryption at rest
# --------------------------------------------------------------------------

def test_chunk_text_is_encrypted_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("EURAG_EMBEDDER", "hash")
    monkeypatch.setenv("EURAG_RERANKER", "none")
    monkeypatch.setenv("EURAG_DATA_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("EURAG_ENCRYPTION_KEY", os.urandom(32).hex())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    p = Pipeline(Settings())
    _upload("MAGICWORD confidential compliance note.", "alice", p)
    # readable through the registry (transparent decrypt)
    assert any("MAGICWORD" in c.text for c in p.registry.all_chunks())
    p.close()

    # but the raw sqlite bytes never contain the plaintext
    raw = (tmp_path / "var" / "registry.sqlite3").read_bytes()
    assert b"MAGICWORD" not in raw


# --------------------------------------------------------------------------
# GDPR Art. 17 erasure
# --------------------------------------------------------------------------

def test_erase_removes_document_from_every_store(settings):
    p = Pipeline(settings)
    doc_id = _upload("ERASEME sensitive text about widgets.", "alice", p)
    assert any("ERASEME" in c.text for c in p.registry.all_chunks())

    assert p.erase_document(doc_id) is True
    assert not any("ERASEME" in c.text for c in p.registry.all_chunks())
    assert p.registry.document_tenant(doc_id) is None
    # gone from BM25 too — no ghost hits
    hits = p.retriever.retrieve("ERASEME widgets", k=10)
    assert doc_id not in {cid.rsplit(":", 1)[0] for cid in hits}
    assert p.erase_document(doc_id) is False  # idempotent
    p.close()


def test_erase_tenant_clears_all_user_docs(settings):
    p = Pipeline(settings)
    _upload("doc one about widgets.", "alice", p, title="A")
    _upload("doc two about gadgets.", "alice", p, title="B")
    _upload("bob keeps this.", "bob", p, title="C")
    assert p.erase_tenant("alice") == 2
    assert p.registry.tenant_doc_ids("alice") == []
    assert p.registry.tenant_doc_ids("bob")  # untouched
    p.close()


# --------------------------------------------------------------------------
# Authenticated API flow
# --------------------------------------------------------------------------

@pytest.fixture()
def auth_client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    from api.main import app

    with TestClient(app) as client:
        yield client


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_protected_routes_require_a_token(auth_client):
    assert auth_client.post("/query", json={"question": "What is an SME?"}).status_code == 401
    assert auth_client.get("/documents").status_code == 401


def test_register_login_query_flow(auth_client):
    assert auth_client.post(
        "/auth/register", json={"username": "alice", "password": "longpassword1"}
    ).json()["role"] == "admin"
    tokens = auth_client.post(
        "/auth/login", json={"username": "alice", "password": "longpassword1"}
    ).json()
    res = auth_client.post(
        "/query", json={"question": "What is an SME?"}, headers=_bearer(tokens["access_token"])
    )
    assert res.status_code == 200
    assert res.json()["mode"] in ("extractive", "llm", "no_sources")


def test_pii_upload_is_rejected_over_api(auth_client):
    tokens = auth_client.post(
        "/auth/register", json={"username": "alice", "password": "longpassword1"}
    )
    tokens = auth_client.post(
        "/auth/login", json={"username": "alice", "password": "longpassword1"}
    ).json()
    res = auth_client.post(
        "/ingest",
        json={"title": "Leak", "text": "Employee ana@corp.eu was dismissed. " * 3},
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 422
    assert "personal data" in res.json()["detail"]


def test_users_cannot_see_or_erase_each_others_uploads(auth_client):
    def user(name):
        auth_client.post("/auth/register", json={"username": name, "password": "longpassword1"})
        return auth_client.post(
            "/auth/login", json={"username": name, "password": "longpassword1"}
        ).json()["access_token"]

    alice, bob = user("alice"), user("bob")
    up = auth_client.post(
        "/ingest",
        json={"title": "Alice plan", "text": "Alice's confidential widget roadmap. " * 5},
        headers=_bearer(alice),
    ).json()
    alice_doc = up["doc_id"]

    # bob's document listing excludes alice's upload
    bob_docs = auth_client.get("/documents", headers=_bearer(bob)).json()["documents"]
    assert all("Alice plan" not in d["title"] for d in bob_docs)

    # bob cannot erase alice's document
    assert auth_client.delete(f"/documents/{alice_doc}", headers=_bearer(bob)).status_code == 403
    # alice can
    assert auth_client.delete(f"/documents/{alice_doc}", headers=_bearer(alice)).status_code == 200


def test_admin_only_audit_log(auth_client):
    def user(name):
        auth_client.post("/auth/register", json={"username": name, "password": "longpassword1"})
        return auth_client.post(
            "/auth/login", json={"username": name, "password": "longpassword1"}
        ).json()["access_token"]

    admin, normal = user("alice"), user("bob")
    assert auth_client.get("/admin/audit", headers=_bearer(normal)).status_code == 403
    entries = auth_client.get("/admin/audit", headers=_bearer(admin)).json()["entries"]
    assert any(e["action"] == "auth.register" for e in entries)
