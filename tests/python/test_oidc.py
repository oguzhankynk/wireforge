from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from wireforge import cli
from wireforge.server.app import create_app
from wireforge.server.oidc import OidcConfig, OidcUnavailableError, OidcVerifier

if TYPE_CHECKING:
    from pathlib import Path

    from typing_extensions import Self

ISSUER = "https://idp.example"
AUDIENCE = "wireforge"
KEY = RSAKey.generate_key(2048, {"kid": "k1"})
OTHER_KEY = RSAKey.generate_key(2048, {"kid": "k1"})


def _token(key: RSAKey = KEY, **overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "u1",
        "email": "u1@example.com",
        "iat": now,
        "exp": now + 300,
    } | overrides
    return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key)


class _Response(io.BytesIO):
    """A urlopen-compatible context manager wrapping one JSON body."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serve the discovery document and the key set of a fake issuer; return the fetched URLs."""
    fetched: list[str] = []

    def fake_urlopen(url: str, **_: object) -> _Response:
        fetched.append(url)
        body: dict[str, object]
        if url == f"{ISSUER}/.well-known/openid-configuration":
            body = {"jwks_uri": f"{ISSUER}/jwks"}
        elif url == f"{ISSUER}/jwks":
            body = {"keys": [KEY.as_dict(private=False)]}
        else:
            message = f"unexpected url {url}"
            raise urllib.error.URLError(message)
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return fetched


def _verifier(jwks_cache_seconds: int = 3600) -> OidcVerifier:
    return OidcVerifier(OidcConfig(ISSUER, AUDIENCE, jwks_cache_seconds))


def test_valid_token(idp: list[str]) -> None:
    user = _verifier().verify(_token())
    assert (user.subject, user.email) == ("u1", "u1@example.com")
    assert len(idp) == 2


@pytest.mark.parametrize(
    "token",
    [
        _token(exp=int(time.time()) - 3600),
        _token(nbf=int(time.time()) + 3600),
        _token(aud="other"),
        _token(iss="https://evil.example"),
        _token(OTHER_KEY),
        "not-a-jwt",
    ],
    ids=["expired", "not-yet-valid", "audience", "issuer", "signature", "garbage"],
)
def test_invalid_token(idp: list[str], token: str) -> None:
    del idp
    with pytest.raises(ValueError, match="invalid token"):
        _verifier().verify(token)


def test_audience_list(idp: list[str]) -> None:
    del idp
    assert _verifier().verify(_token(aud=["a", AUDIENCE])).subject == "u1"


def test_clock_skew(idp: list[str]) -> None:
    del idp
    assert _verifier().verify(_token(exp=int(time.time()) - 30)).subject == "u1"


def test_jwks_cache(idp: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _verifier(100)
    verifier.verify(_token())
    verifier.verify(_token())
    assert len(idp) == 2, "second check uses the cached keys"
    clock = time.monotonic() + 101
    monkeypatch.setattr(time, "monotonic", lambda: clock)
    verifier.verify(_token())
    assert len(idp) == 4, "stale keys are fetched again"


def test_issuer_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> _Response:
        message = "refused"
        raise urllib.error.URLError(message)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(OidcUnavailableError):
        _verifier().verify(_token())


def test_server_with_oidc(tmp_path: Path, idp: list[str]) -> None:
    del idp
    client = TestClient(create_app(tmp_path, oidc=OidcConfig(ISSUER, AUDIENCE)))
    assert client.get("/api/health").status_code == 200, "health needs no token"
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Basic x"}).status_code == 401
    bad = {"Authorization": f"Bearer {_token(aud='other')}"}
    assert client.get("/api/projects", headers=bad).status_code == 401
    good = {"Authorization": f"Bearer {_token()}"}
    assert client.get("/api/projects", headers=good).status_code == 200


def test_server_with_unreachable_issuer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> _Response:
        message = "refused"
        raise urllib.error.URLError(message)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    client = TestClient(create_app(tmp_path, oidc=OidcConfig(ISSUER, AUDIENCE)))
    headers = {"Authorization": f"Bearer {_token()}"}
    assert client.get("/api/projects", headers=headers).status_code == 503


def test_cli_options(tmp_path: Path) -> None:
    base = ["serve", "--workspace", str(tmp_path), "--oidc-issuer", ISSUER]
    assert cli.main([*base, "--oidc-audience", AUDIENCE, "--token", "t"]) == cli.EXIT_USAGE
    assert cli.main(base) == cli.EXIT_USAGE
