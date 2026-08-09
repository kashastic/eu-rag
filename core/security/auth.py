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
    username     TEXT PRIMARY KEY,
    salt         TEXT NOT NULL,
    pw_hash      TEXT NOT NULL,
    role         TEXT NOT NULL,
    tenant       TEXT NOT NULL,
    created_at   DOUBLE PRECISION NOT NULL,
    byok_key_enc TEXT,
    byok_set_at  DOUBLE PRECISION,
    google_sub   TEXT,
    email        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS users_google_sub ON users (google_sub);
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
        # migrate older databases in place — one ALTER per added column, each
        # independently guarded so a DB that has one but not the other still
        # gets the missing one
        for ddl in (
            "ALTER TABLE users ADD COLUMN byok_key_enc TEXT",
            "ALTER TABLE users ADD COLUMN byok_set_at DOUBLE PRECISION",
            "ALTER TABLE users ADD COLUMN google_sub TEXT",
            "ALTER TABLE users ADD COLUMN email TEXT",
            # NULLs don't collide in a unique index on either backend, so
            # password-only accounts are unaffected
            "CREATE UNIQUE INDEX IF NOT EXISTS users_google_sub ON users (google_sub)",
        ):
            try:
                db.execute(ddl)
                if not db.is_pg:
                    db._conn.commit()
            except Exception:
                pass  # already present

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
        # A Google account stores an empty pw_hash — there is no password to
        # get right, so password login must be refused outright rather than
        # compared against a value no input can produce.
        if row is not None and not row["pw_hash"]:
            self.audit(username, "auth.login_failed", detail="password login on a google account")
            raise AuthError("invalid credentials")
        if row is None or not hmac.compare_digest(
            _hash_password(password, bytes.fromhex(row["salt"])),
            bytes.fromhex(row["pw_hash"]),
        ):
            self.audit(username, "auth.login_failed")
            raise AuthError("invalid credentials")
        self.audit(username, "auth.login")
        return Principal(row["username"], row["role"], row["tenant"])

    # --- Google sign-in ------------------------------------------------------

    def upsert_google_user(
        self, google_sub: str, email: str, name: str | None = None
    ) -> Principal:
        """Find or create the account for a verified Google identity.

        Keyed on `google_sub` and **only** on `google_sub`. It is deliberately
        impossible to reach an existing account this way: a Google login never
        matches on username or email, and a derived username that is already
        taken is skipped rather than reused. Otherwise anyone could register the
        username `alice` (or an account with alice's address) and wait for the
        real alice to sign in with Google — account takeover by land-grab.
        Linking a Google identity to a password account would need proof of
        ownership of that account, and existing accounts have no email to prove
        it with, so the two are simply separate.
        """
        row = self._db.query_one(
            "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
        )
        if row is not None:
            if email and email != row["email"]:
                with self._db.transaction() as tx:  # keep the shown address fresh
                    tx.execute(
                        "UPDATE users SET email = ? WHERE username = ?",
                        (email, row["username"]),
                    )
            self.audit(row["username"], "auth.google_login")
            return Principal(row["username"], row["role"], row["tenant"])

        username = self._free_username(email, name)
        first_user = not self._db.query_one("SELECT 1 AS x FROM users LIMIT 1")
        role = "admin" if first_user else "user"
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO users"
                " (username, salt, pw_hash, role, tenant, created_at, google_sub, email)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    os.urandom(16).hex(),
                    "",  # no password — see the guard in authenticate()
                    role,
                    username,
                    time.time(),
                    google_sub,
                    email or None,
                ),
            )
        self.audit(username, "auth.google_register", detail=role)
        return Principal(username, role, username)

    def _free_username(self, email: str, name: str | None) -> str:
        """A username derived from the Google profile that satisfies the same
        rule as registration (3-40 chars, letters/digits/underscore) and is not
        already taken."""
        raw = (email.split("@")[0] if email else "") or (name or "") or "user"
        # ASCII only: str.isalnum() is true for "ë" and every other unicode
        # letter, and a username derived from a display name ends up in a tenant
        # id and a URL. Keep it boring.
        base = "".join(
            c
            for c in raw.lower().replace(" ", "_")
            if c.isascii() and (c.isalnum() or c == "_")
        )
        base = (base or "user")[:36]
        while len(base) < 3:
            base += "0"
        for suffix in ("", *(str(n) for n in range(2, 10000))):
            candidate = base + suffix
            if not self._db.query_one(
                "SELECT 1 AS x FROM users WHERE username = ?", (candidate,)
            ):
                return candidate
        raise AuthError("could not derive a free username")  # pragma: no cover

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

    # --- BYOK (store the already-encrypted blob; crypto happens in the route) --

    def set_byok(self, username: str, encrypted: str) -> None:
        with self._db.transaction() as tx:
            tx.execute(
                "UPDATE users SET byok_key_enc = ?, byok_set_at = ? WHERE username = ?",
                (encrypted, time.time(), username),
            )

    def clear_byok(self, username: str) -> None:
        with self._db.transaction() as tx:
            tx.execute(
                "UPDATE users SET byok_key_enc = NULL, byok_set_at = NULL"
                " WHERE username = ?",
                (username,),
            )

    def get_byok(self, username: str) -> str | None:
        row = self._db.query_one(
            "SELECT byok_key_enc FROM users WHERE username = ?", (username,)
        )
        return row["byok_key_enc"] if row else None

    def byok_set_at(self, username: str) -> float | None:
        """When the stored key was last set — the UI turns this into "added N
        days ago" so a user can be nudged to rotate a key they've forgotten
        about. Never the key itself."""
        row = self._db.query_one(
            "SELECT byok_set_at FROM users WHERE username = ?", (username,)
        )
        return row["byok_set_at"] if row else None

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
