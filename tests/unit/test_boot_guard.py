"""validate_startup: refuse multi-instance boot with missing shared secrets.

The matrix that matters: raising is reserved for auth + Postgres + no JWT
secret (per-instance auto-secrets silently break cross-replica login);
everything local stays zero-config; a missing encryption key only warns."""

import pytest

from core.config import Settings, validate_startup

PG = "postgresql://eurag:pw@postgres:5432/eurag"
KEY = "ab" * 32


def test_auth_on_postgres_without_jwt_secret_refuses_boot():
    s = Settings(auth_enabled=True, jwt_secret=None, encryption_key=KEY)
    with pytest.raises(RuntimeError, match="EURAG_JWT_SECRET"):
        validate_startup(s, PG)


def test_auth_on_postgres_with_jwt_secret_boots():
    s = Settings(auth_enabled=True, jwt_secret="x" * 32, encryption_key=KEY)
    validate_startup(s, PG)


def test_auth_off_is_never_gated():
    # local zero-config mode: nothing set, nothing raised, nothing logged
    s = Settings(auth_enabled=False, jwt_secret=None, encryption_key=None)
    validate_startup(s, None)
    validate_startup(s, PG)


def test_auth_on_sqlite_without_jwt_secret_boots():
    # single instance on SQLite: the auto-generated per-instance secret works
    s = Settings(auth_enabled=True, jwt_secret=None, encryption_key=KEY)
    validate_startup(s, None)
    validate_startup(s, "sqlite:///var/eurag.sqlite3")


def test_missing_encryption_key_warns_but_boots(caplog):
    s = Settings(auth_enabled=True, jwt_secret="x" * 32, encryption_key=None)
    with caplog.at_level("WARNING"):
        validate_startup(s, PG)
    assert "EURAG_ENCRYPTION_KEY" in caplog.text


def test_no_warning_when_encryption_key_set(caplog):
    s = Settings(auth_enabled=True, jwt_secret="x" * 32, encryption_key=KEY)
    with caplog.at_level("WARNING"):
        validate_startup(s, PG)
    assert caplog.text == ""
