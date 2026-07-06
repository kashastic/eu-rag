"""Users, JWTs, and the append-only audit log.

- Passwords: scrypt (stdlib), per-user random salt.
- Tokens: HS256 JWTs. Short-lived access tokens carry sub/role/tenant;
  refresh tokens are single-use (rotated on refresh, revoked on use) and
  tracked by jti so a stolen refresh token dies on first reuse.
- Roles: the FIRST registered user becomes "admin", everyone after "user".
- Tenancy: every user gets a private tenant named after them; the shared
  official corpus lives in tenant "public" and is readable by everyone.
- Audit: append-only table (guarded by SQLite triggers) recording who did
  what, when. Question texts are stored as SHA-256 hashes, not plaintext —
  queries can themselves contain personal data, and erasure must never
  require editing the audit trail.
"""

import hashlib
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import jwt

logger = logging.getLogger(__name__)

ACCESS_TTL = 15 * 60  # seconds
REFRESH_TTL = 7 * 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    salt       BLOB NOT NULL,
    pw_hash    BLOB NOT NULL,
    role       TEXT NOT NULL,
    tenant     TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti        TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    expires_at REAL NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    detail   TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
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
    """JWT secret from env, else a persisted random one (dev convenience)."""
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
    def __init__(self, path: Path, jwt_secret: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._secret = jwt_secret

    # --- users ---------------------------------------------------------------

    def register(self, username: str, password: str) -> Principal:
        username = username.strip().lower()
        if not (3 <= len(username) <= 40) or not username.replace("_", "").isalnum():
            raise AuthError("username: 3-40 chars, letters/digits/underscore")
        if len(password) < 10:
            raise AuthError("password must be at least 10 characters")
        first_user = self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        role = "admin" if first_user else "user"
        salt = os.urandom(16)
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (username, salt, _hash_password(password, salt), role, username, time.time()),
                )
        except sqlite3.IntegrityError:
            raise AuthError("username already taken") from None
        self.audit(username, "auth.register", detail=role)
        return Principal(username, role, username)

    def authenticate(self, username: str, password: str) -> Principal:
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
        if row is None or not _constant_eq(
            _hash_password(password, row["salt"]), row["pw_hash"]
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
        with self._conn:
            self._conn.execute(
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
        """Single-use rotation: the presented refresh token is revoked and a
        fresh pair issued. A revoked/unknown jti is rejected."""
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid token: {exc}") from None
        if claims.get("type") != "refresh":
            raise AuthError("not a refresh token")
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1"
                " WHERE jti = ? AND revoked = 0 AND expires_at > ?",
                (claims["jti"], time.time()),
            )
        if cursor.rowcount != 1:
            self.audit(claims.get("sub", "?"), "auth.refresh_reuse_blocked")
            raise AuthError("refresh token expired, revoked, or reused")
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (claims["sub"],)
        ).fetchone()
        if row is None:
            raise AuthError("user no longer exists")
        self.audit(claims["sub"], "auth.refresh")
        return self.issue_tokens(Principal(row["username"], row["role"], row["tenant"]))

    # --- audit ---------------------------------------------------------------

    def audit(self, actor: str, action: str, resource: str = "", detail: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO audit (ts, actor, action, resource, detail) VALUES (?, ?, ?, ?, ?)",
                (time.time(), actor, action, resource, detail),
            )

    def audit_entries(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()


def _constant_eq(a: bytes, b: bytes) -> bool:
    import hmac

    return hmac.compare_digest(a, b)
