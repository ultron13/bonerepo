"""An OpenID Connect provider, just enough of one to sign somebody in.

Single sign-on cannot be tested or demonstrated without a provider, and
pointing `make dev` at a real one would put somebody else's service on the
critical path of a local start-up -- the same reasoning as the Git fixture
beside it.

Deliberately not a general-purpose provider. It signs real RS256 tokens with a
real key and publishes a real JWKS, because those are the parts the system
under test actually checks. Everything else is the shortest path to a code.

Behaviour can be steered per-request through the authorize query string, so a
test can ask for the tokens that should be refused:

    ?plimsoll_email_verified=false   an address the provider has not verified
    ?plimsoll_nonce=other            a token minted for a different sign-in
    ?plimsoll_groups=a,b             group membership for role mapping
    ?plimsoll_email=someone@x.test   who signs in
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = os.environ.get("IDP_ISSUER", "http://idp-fixture")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "idp-fixture-key"

# code -> the claims that code will be exchanged for.
PENDING: dict[str, dict[str, object]] = {}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwks() -> dict[str, object]:
    numbers = KEY.public_key().public_numbers()

    def encode(value: int) -> str:
        return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": encode(numbers.n),
                "e": encode(numbers.e),
            }
        ]
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        return

    def _send(self, status: int, body: dict[str, object] | None = None, **headers: str) -> None:
        payload = json.dumps(body or {}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = {key: value[0] for key, value in parse_qs(url.query).items()}

        if url.path == "/.well-known/openid-configuration":
            self._send(
                200,
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
            return

        if url.path == "/jwks":
            self._send(200, _jwks())
            return

        if url.path == "/authorize":
            code = uuid.uuid4().hex
            groups = query.get("plimsoll_groups", "")
            PENDING[code] = {
                "aud": query.get("client_id", ""),
                "nonce": query.get("plimsoll_nonce") or query.get("nonce", ""),
                "email": query.get("plimsoll_email", "sso-user@example.com"),
                "email_verified": query.get("plimsoll_email_verified", "true") == "true",
                "name": query.get("plimsoll_name", "SSO User"),
                "groups": [g for g in groups.split(",") if g],
                "sub": query.get("plimsoll_sub", "idp-fixture-subject"),
                # PKCE, recorded so the token endpoint can refuse a verifier
                # that does not match -- which is the only reason PKCE is worth
                # having, and so worth a fixture that checks it.
                "code_challenge": query.get("code_challenge", ""),
            }
            redirect = query.get("redirect_uri", "")
            separator = "&" if "?" in redirect else "?"
            self.send_response(302)
            self.send_header(
                "location",
                f"{redirect}{separator}"
                + urlencode({"code": code, "state": query.get("state", "")}),
            )
            self.send_header("content-length", "0")
            self.end_headers()
            return

        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/token":
            self._send(404, {"error": "not_found"})
            return

        length = int(self.headers.get("content-length", "0"))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        claims = PENDING.pop(form.get("code", ""), None)
        if claims is None:
            self._send(400, {"error": "invalid_grant"})
            return

        expected = claims.pop("code_challenge", "")
        if expected:
            verifier = form.get("code_verifier", "")
            actual = _b64(hashlib.sha256(verifier.encode()).digest())
            if actual != expected:
                self._send(400, {"error": "invalid_grant", "error_description": "bad verifier"})
                return

        now = int(time.time())
        token = jwt.encode(
            {"iss": ISSUER, "iat": now, "exp": now + 300, **claims},
            KEY,
            algorithm="RS256",
            headers={"kid": KID},
        )
        self._send(200, {"id_token": token, "token_type": "Bearer", "expires_in": 300})


if __name__ == "__main__":
    # A container on a private network, reached by service name.
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()  # noqa: S104
