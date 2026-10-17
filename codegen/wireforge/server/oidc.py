"""OIDC bearer-token verification: discovery, JWKS, JWT validation.

Nothing is persisted: every check is stateless beyond an in-memory JWKS cache.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512")
CLOCK_SKEW_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 5.0


class OidcUnavailableError(RuntimeError):
    """The issuer's discovery document or key set could not be fetched."""


@dataclass(frozen=True)
class OidcConfig:
    """Issuer and audience a bearer token must carry."""

    issuer: str
    audience: str
    jwks_cache_seconds: int = 3600


@dataclass(frozen=True)
class OidcUser:
    """Verified claims of one request; never persisted."""

    subject: str
    email: str | None = None
    username: str | None = None


class OidcVerifier:
    """Fetches and caches the issuer's JWKS; verifies bearer tokens against it."""

    def __init__(self, config: OidcConfig) -> None:
        """Start with an empty key cache."""
        self.config = config
        self._keys: KeySet | None = None
        self._fetched_at = 0.0

    def _refresh_if_stale(self) -> KeySet:
        keys = self._keys
        age = time.monotonic() - self._fetched_at
        if keys is not None and age < self.config.jwks_cache_seconds:
            return keys
        url = f"{self.config.issuer.rstrip('/')}/.well-known/openid-configuration"
        try:
            discovery = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS).raise_for_status().json()
            jwks_response = httpx.get(discovery["jwks_uri"], timeout=HTTP_TIMEOUT_SECONDS)
            keys = KeySet.import_key_set(jwks_response.raise_for_status().json())
        except (httpx.HTTPError, ValueError, KeyError, JoseError) as error:
            message = f"cannot fetch the OIDC keys of {self.config.issuer}: {error}"
            raise OidcUnavailableError(message) from error
        self._keys = keys
        self._fetched_at = time.monotonic()
        return keys

    def verify(self, bearer_token: str) -> OidcUser:
        """Return the token's user; raise ValueError on any failure (the caller maps it to 401)."""
        keys = self._refresh_if_stale()
        try:
            claims = jwt.decode(bearer_token, keys, algorithms=ALGORITHMS).claims
            registry = jwt.JWTClaimsRegistry(
                leeway=CLOCK_SKEW_SECONDS,
                iss={"essential": True, "value": self.config.issuer},
                aud={"essential": True, "value": self.config.audience},
                sub={"essential": True},
                exp={"essential": True},
            )
            registry.validate(claims)
        except (JoseError, ValueError) as error:
            message = f"invalid token: {error}"
            raise ValueError(message) from error
        return OidcUser(
            subject=str(claims["sub"]),
            email=claims.get("email"),
            username=claims.get("preferred_username"),
        )
