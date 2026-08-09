"""Google Sign-In: ID-token verification and the account it maps to.

Fully offline — a locally generated RSA key stands in for Google's, and the
JWKS fetch seam (`google_oauth._signing_key`) is monkeypatched. That seam exists
for this reason; see the module docstring.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from core.db import Database
from core.security import google_oauth
from core.security.auth import AuthError, AuthStore

CLIENT_ID = "test-client-id.apps.googleusercontent.com"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture()
def sign(keypair, monkeypatch):
    private, public = keypair
    monkeypatch.setattr(google_oauth, "_signing_key", lambda token: public)

    def _sign(**overrides):
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-user-1",
            "email": "alice@example.com",
            "email_verified": True,
            "name": "Alice Example",
            "iat": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        return jwt.encode(
            {k: v for k, v in claims.items() if v is not None}, private, algorithm="RS256"
        )

    return _sign


# --- the verification boundary --------------------------------------------

def test_valid_token_verifies(sign):
    claims = google_oauth.verify_id_token(sign(), CLIENT_ID)
    assert claims["sub"] == "google-user-1"
    assert claims["email"] == "alice@example.com"


def test_token_for_another_app_is_rejected(sign):
    """A perfectly valid Google token minted for someone else's client id is
    still not a login here — this is the check that makes `aud` load-bearing."""
    other = sign(aud="someone-elses-app.apps.googleusercontent.com")
    with pytest.raises(google_oauth.GoogleAuthError):
        google_oauth.verify_id_token(other, CLIENT_ID)


def test_unverified_email_is_rejected(sign):
    with pytest.raises(google_oauth.GoogleAuthError, match="verified email"):
        google_oauth.verify_id_token(sign(email_verified=False), CLIENT_ID)


def test_expired_token_is_rejected(sign):
    past = int(time.time()) - 7200
    with pytest.raises(google_oauth.GoogleAuthError):
        google_oauth.verify_id_token(sign(iat=past, exp=past + 60), CLIENT_ID)


def test_wrong_issuer_is_rejected(sign):
    with pytest.raises(google_oauth.GoogleAuthError, match="issuer"):
        google_oauth.verify_id_token(sign(iss="https://evil.example.com"), CLIENT_ID)


def test_token_signed_by_someone_else_is_rejected(sign, monkeypatch):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-user-1",
            "email": "alice@example.com",
            "email_verified": True,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        attacker,
        algorithm="RS256",
    )
    with pytest.raises(google_oauth.GoogleAuthError):
        google_oauth.verify_id_token(forged, CLIENT_ID)


def test_unsigned_token_is_rejected(sign):
    """alg=none must never be honoured — algorithms are pinned to RS256."""
    unsigned = jwt.encode(
        {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "x",
         "email_verified": True, "iat": int(time.time()), "exp": int(time.time()) + 60},
        key="",
        algorithm="none",
    )
    with pytest.raises(google_oauth.GoogleAuthError):
        google_oauth.verify_id_token(unsigned, CLIENT_ID)


def test_empty_credential_and_missing_client_id(sign):
    with pytest.raises(google_oauth.GoogleAuthError):
        google_oauth.verify_id_token("", CLIENT_ID)
    with pytest.raises(google_oauth.GoogleAuthError):
        google_oauth.verify_id_token(sign(), "")


# --- the account it maps to ------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    return AuthStore(Database(None, sqlite_path=tmp_path / "a.db"), "s" * 32)


def test_google_user_is_created_once_and_reused(store):
    first = store.upsert_google_user("sub-1", "alice@example.com", "Alice")
    again = store.upsert_google_user("sub-1", "alice@example.com", "Alice")
    assert first.username == again.username == "alice"
    assert first.role == "admin"  # first account on the instance, same as register


def test_second_google_user_gets_a_distinct_username(store):
    a = store.upsert_google_user("sub-1", "alice@example.com")
    b = store.upsert_google_user("sub-2", "alice@other.com")
    assert a.username == "alice" and b.username == "alice2"
    assert a.tenant != b.tenant  # separate private corpora


def test_google_login_never_lands_on_an_existing_password_account(store):
    """The land-grab: register `alice` with a password, then have the real
    alice@example.com sign in with Google. She must NOT be given that account."""
    squatter = store.register("alice", "longpassword1")
    victim = store.upsert_google_user("sub-1", "alice@example.com", "Alice")
    assert victim.username != squatter.username
    assert victim.username == "alice2"


def test_password_login_is_refused_on_a_google_account(store):
    store.upsert_google_user("sub-1", "alice@example.com")
    with pytest.raises(AuthError):
        store.authenticate("alice", "")
    with pytest.raises(AuthError):
        store.authenticate("alice", "anything-at-all")


def test_email_is_refreshed_when_google_reports_a_new_one(store):
    store.upsert_google_user("sub-1", "old@example.com")
    store.upsert_google_user("sub-1", "new@example.com")
    row = store._db.query_one("SELECT email FROM users WHERE google_sub = ?", ("sub-1",))
    assert row["email"] == "new@example.com"


def test_awkward_profiles_still_yield_a_valid_username(store):
    assert store.upsert_google_user("s1", "a.b-c+tag@example.com").username == "abctag"
    assert store.upsert_google_user("s2", "x@example.com").username == "x00"
    assert store.upsert_google_user("s3", "", "Zoë Q").username == "zo_q"


# --- the route -------------------------------------------------------------

@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_GOOGLE_CLIENT_ID", CLIENT_ID)
    from api.main import app

    with TestClient(app) as c:
        yield c


def test_route_issues_a_session_and_healthz_advertises_the_client_id(client, sign):
    assert client.get("/healthz").json()["google_client_id"] == CLIENT_ID
    r = client.post("/auth/google", json={"credential": sign()})
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200 and me.json()["username"] == "alice"


def test_route_rejects_a_bad_credential(client, sign):
    r = client.post("/auth/google", json={"credential": sign(aud="another-app")})
    assert r.status_code == 401


def test_route_is_503_when_google_is_not_configured(settings, monkeypatch, sign):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.delenv("EURAG_GOOGLE_CLIENT_ID", raising=False)
    from api.main import app

    with TestClient(app) as c:
        assert c.get("/healthz").json()["google_client_id"] is None
        r = c.post("/auth/google", json={"credential": sign()})
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "google_disabled"
