"""Google Sign-In — verifying the ID token the browser receives from Google.

We use the Google Identity Services **ID-token** flow, not the authorization-code
flow, because EURAG only ever wants an *identity*: it never calls a Google API
on the user's behalf. That choice deletes a whole category of things to hold
safely — there is **no client secret**, no redirect/callback route, and no state
or PKCE to store server-side. The browser gets a short-lived JWT signed by
Google; this module decides whether to believe it.

Verification is therefore the entire security boundary, so it is strict:

- **signature** against Google's published JWKS (RS256 only — never trust the
  token's own `alg`),
- **`aud` must equal our client id.** A token minted for some other app is a
  valid Google token and still not a login here; this is the check that stops
  one.
- **`iss` must be Google**,
- **`exp`/`iat`** enforced by PyJWT,
- **`email_verified` must be true** — otherwise someone could claim an address
  they don't own, and the address is what a human reads as the identity.

The identity we key on is `sub`, never the email: Google guarantees `sub` is
stable and never reused, while an email can change hands.

The JWKS fetch sits behind a module-level seam (`_signing_key`) for exactly the
reason `turnstile._post` does — the test suite must run offline.
"""

import jwt
from jwt import PyJWKClient

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleAuthError(ValueError):
    """The credential is not a token we will accept as a login."""


_jwk_client: PyJWKClient | None = None


def _signing_key(token: str):
    """Google's public signing key for this token. Module-level so tests can
    monkeypatch the network away; PyJWKClient caches keys between calls, so
    steady-state sign-ins do not re-fetch the JWKS."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True)
    return _jwk_client.get_signing_key_from_jwt(token).key


def verify_id_token(credential: str, client_id: str) -> dict:
    """Return the verified claims, or raise GoogleAuthError.

    Fails **closed** on everything, including an unreachable JWKS: unlike the
    Turnstile check (which fails open because the per-IP quota still bounds
    abuse), this one mints a session. There is no safe way to wave it through.
    """
    if not credential:
        raise GoogleAuthError("no Google credential supplied")
    if not client_id:
        raise GoogleAuthError("server has no Google client id configured")
    try:
        claims = jwt.decode(
            credential,
            _signing_key(credential),
            algorithms=["RS256"],
            audience=client_id,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except Exception as exc:  # bad signature, wrong aud, expired, unreachable
        raise GoogleAuthError(f"invalid Google credential: {exc}") from None
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleAuthError(f"unexpected issuer {claims.get('iss')!r}")
    if not claims.get("sub"):
        raise GoogleAuthError("credential carries no subject")
    if not claims.get("email_verified"):
        raise GoogleAuthError("that Google account has no verified email address")
    return claims
