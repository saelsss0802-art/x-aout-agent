from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

X_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
DEFAULT_SCOPES = ["tweet.write", "users.read", "offline.access", "tweet.read"]


class XOAuthError(RuntimeError):
    pass


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise XOAuthError(f"{name} is required")
    return value


def generate_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return code_verifier, code_challenge


def build_authorize_url(
    *,
    state: str,
    code_challenge: str,
    scopes: list[str] | None = None,
    redirect_uri: str | None = None,
) -> str:
    client_id = _require_env("X_OAUTH_CLIENT_ID")
    callback = redirect_uri or _require_env("X_OAUTH_REDIRECT_URI")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback,
        "scope": " ".join(scopes or DEFAULT_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{X_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(
    *,
    code: str,
    code_verifier: str,
    http_client: httpx.Client | None = None,
) -> dict[str, object]:
    client = http_client or httpx.Client(timeout=15.0)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _require_env("X_OAUTH_REDIRECT_URI"),
        "client_id": _require_env("X_OAUTH_CLIENT_ID"),
        "code_verifier": code_verifier,
    }
    return _token_request(client=client, payload=payload)


def refresh_access_token(
    *,
    refresh_token: str,
    http_client: httpx.Client | None = None,
) -> dict[str, object]:
    client = http_client or httpx.Client(timeout=15.0)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _require_env("X_OAUTH_CLIENT_ID"),
    }
    return _token_request(client=client, payload=payload)


def _token_request(*, client: httpx.Client, payload: dict[str, str]) -> dict[str, object]:
    client_secret = os.getenv("X_OAUTH_CLIENT_SECRET")
    auth = None
    if client_secret:
        auth = (payload["client_id"], client_secret)

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        response = client.post(X_TOKEN_URL, data=payload, headers=headers, auth=auth)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise XOAuthError(f"x_oauth_token_request_failed:{exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise XOAuthError(f"x_oauth_token_request_network_error:{exc.__class__.__name__}") from exc

    parsed = response.json()
    if not isinstance(parsed, dict):
        raise XOAuthError("x_oauth_token_response_invalid")
    return parsed


def build_state() -> str:
    return secrets.token_urlsafe(32)


def state_expiry(minutes: int = 10) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
