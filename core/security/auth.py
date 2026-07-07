"""Users, JWTs, and the audit log — on the shared Database.

- Passwords: scrypt (stdlib), per-user random salt, stored as hex.
- Tokens: HS256 JWTs. Access tokens carry sub/role/tenant and are validated
  statelessly by any instance sharing EURAG_JWT_SECRET. Refresh tokens are
  single-use (rotated on refresh, revoked by jti in shared storage) so a
  stolen refresh token dies on first reuse across the whole fleet.
- Roles: first registered user is "admin", everyone after "user".
- Tenancy: each user gets a private tenant; the shared official corpus is
  tenant "public".
- Audit: append-only by discipline (the store exposes no update/delete for
  it). Question texts are stored as SHA-256 hashes, never plaintext.

Running on Postgres (EURAG_DATABASE_URL) makes login, refresh-token
revocation, and the audit trail consistent across every app instance.
"""

import hashlib
import hmac
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import jwt

from core.db import Database

logger = logging.getLogger(__name__)

ACCESS_TTL = 15 * 60  # seconds
REFRESH_TTL = 7 * 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    salt       TEXT NOT NULL,
    pw_hash    TEXT NOT NULL,
    role       TEXT NOT NULL,
    tenant     TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti        TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id       {serial},
    ts       DOUBLE PRECISION NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    detail   TEXT NOT NULL DEFAULT ''
);
"""


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    username: str
    role: str  # "admin" | "user"
    tenant: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


LOCAL_PRINCIPAL = Principal("local", "admin", "public")  # auth-off mode


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def question_hash(question: str) -> str:
    return hashlib.sha256(question.encode()).hexdigest()[:16]


def load_or_create_secret(path: Path, env_value: str | None) -> str:
    """JWT secret from env (required for multi-instance — the fleet must
    share it), else a persisted random one for local single-instance dev."""
    if env_value:
        return env_value
    if path.is_file():
        return path.read_text().strip()
    secret = os.urandom(32).hex()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret)
    path.chmod(0o600)
    logger.info("generated JWT secret at %s — set EURAG_JWT_SECRET in production", path)
    return secret


class AuthStore:
    def __init__(self, db: Database, jwt_secret: str):
        self._db = db
        self._secret = jwt_secret
        db.executescript(_SCHEMA)

    # --- users ---------------------------------------------------------------

    def register(self, username: str, password: str) -> Principal:
        username = username.strip().lower()
        if not (3 <= len(username) <= 40) or not username.replace("_", "").isalnum():
            raise AuthError("username: 3-40 chars, letters/digits/underscore")
        if len(password) < 10:
            raise AuthError("password must be at least 10 characters")
        if self._db.query_one("SELECT 1 AS x FROM users WHERE username = ?", (username,)):
            raise AuthError("username already taken")
        first_user = not self._db.query_one("SELECT 1 AS x FROM users LIMIT 1")
        role = "admin" if first_user else "user"
        salt = os.urandom(16)
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO users (username, salt, pw_hash, role, tenant, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    username,
                    salt.hex(),
                    _hash_password(password, salt).hex(),
                    role,
                    username,
                    time.time(),
                ),
            )
        self.audit(username, "auth.register", detail=role)
        return Principal(username, role, username)

    def authenticate(self, username: str, password: str) -> Principal:
        row = self._db.query_one(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        )
        if row is None or not hmac.compare_digest(
            _hash_password(password, bytes.fromhex(row["salt"])),
            bytes.fromhex(row["pw_hash"]),
        ):
            self.audit(username, "auth.login_failed")
            raise AuthError("invalid credentials")
        self.audit(username, "auth.login")
        return Principal(row["username"], row["role"], row["tenant"])

    # --- tokens --------------------------------------------------------------

    def issue_tokens(self, principal: Principal) -> dict:
        now = int(time.time())
        access = jwt.encode(
            {
                "sub": principal.username,
                "role": principal.role,
                "tenant": principal.tenant,
                "type": "access",
                "exp": now + ACCESS_TTL,
            },
            self._secret,
            algorithm="HS256",
        )
        jti = uuid.uuid4().hex
        refresh = jwt.encode(
            {"sub": principal.username, "type": "refresh", "jti": jti, "exp": now + REFRESH_TTL},
            self._secret,
            algorithm="HS256",
        )
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO refresh_tokens (jti, username, expires_at) VALUES (?, ?, ?)",
                (jti, principal.username, now + REFRESH_TTL),
            )
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}

    def verify_access(self, token: str) -> Principal:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid token: {exc}") from None
        if claims.get("type") != "access":
            raise AuthError("not an access token")
        return Principal(claims["sub"], claims["role"], claims["tenant"])

    def refresh(self, token: str) -> dict:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid token: {exc}") from None
        if claims.get("type") != "refresh":
            raise AuthError("not a refresh token")
        # single-use: the UPDATE flips revoked 0→1 atomically; rowcount==1 only
        # for the first presenter, so a reused/stolen token gets rowcount 0
        cur = self._db.execute(
            "UPDATE refresh_tokens SET revoked = 1"
            " WHERE jti = ? AND revoked = 0 AND expires_at > ?",
            (claims["jti"], time.time()),
        )
        if not self._db.is_pg:
            self._db._conn.commit()
        if cur.rowcount != 1:
            self.audit(claims.get("sub", "?"), "auth.refresh_reuse_blocked")
            raise AuthError("refresh token expired, revoked, or reused")
        row = self._db.query_one(
            "SELECT * FROM users WHERE username = ?", (claims["sub"],)
        )
        if row is None:
            raise AuthError("user no longer exists")
        self.audit(claims["sub"], "auth.refresh")
        return self.issue_tokens(Principal(row["username"], row["role"], row["tenant"]))

    # --- audit ---------------------------------------------------------------

    def audit(self, actor: str, action: str, resource: str = "", detail: str = "") -> None:
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO audit (ts, actor, action, resource, detail)"
                " VALUES (?, ?, ?, ?, ?)",
                (time.time(), actor, action, resource, detail),
            )

    def audit_entries(self, limit: int = 100) -> list[dict]:
        return self._db.query(
            "SELECT id, ts, actor, action, resource, detail FROM audit"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    def close(self) -> None:
        self._db.close()
