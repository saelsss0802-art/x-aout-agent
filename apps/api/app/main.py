from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from core.models import Account, OAuthState, XAuthToken

from .db import SessionLocal
from .oauth.x_oauth import (
    XOAuthError,
    build_authorize_url,
    build_state,
    exchange_code_for_token,
    generate_pkce_pair,
    refresh_access_token,
    state_expiry,
)

app = FastAPI(title="x-aout-agent-api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _apply_token_payload(account_id: int, payload: dict[str, object]) -> XAuthToken:
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    scope = payload.get("scope")
    token_type = payload.get("token_type")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise HTTPException(status_code=502, detail="x_oauth_token_invalid")
    if not isinstance(expires_in, (int, float)):
        raise HTTPException(status_code=502, detail="x_oauth_expires_invalid")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    scope_text = scope if isinstance(scope, str) else ""
    token_type_text = token_type if isinstance(token_type, str) else "bearer"

    with SessionLocal() as session:
        token = session.scalar(select(XAuthToken).where(XAuthToken.account_id == account_id))
        if token is None:
            token = XAuthToken(
                account_id=account_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scope=scope_text,
                token_type=token_type_text,
            )
            session.add(token)
        else:
            token.access_token = access_token
            token.refresh_token = refresh_token
            token.expires_at = expires_at
            token.scope = scope_text
            token.token_type = token_type_text
        session.commit()
        session.refresh(token)
        return token


@app.get("/oauth/x/start")
def oauth_x_start(account_id: int = Query(..., ge=1)):
    state = build_state()
    code_verifier, code_challenge = generate_pkce_pair()

    with SessionLocal() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="account_not_found")
        session.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.now(timezone.utc)))
        session.add(
            OAuthState(
                account_id=account_id,
                state=state,
                code_verifier=code_verifier,
                expires_at=state_expiry(),
            )
        )
        session.commit()

    authorize_url = build_authorize_url(state=state, code_challenge=code_challenge)
    return RedirectResponse(authorize_url, status_code=302)


@app.get("/oauth/x/callback")
def oauth_x_callback(state: str, code: str):
    with SessionLocal() as session:
        saved_state = session.scalar(select(OAuthState).where(OAuthState.state == state))
        if saved_state is None:
            raise HTTPException(status_code=400, detail="oauth_state_invalid")
        expires_at = saved_state.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="oauth_state_invalid")
        account_id = saved_state.account_id
        verifier = saved_state.code_verifier
        session.delete(saved_state)
        session.commit()

    try:
        payload = exchange_code_for_token(code=code, code_verifier=verifier)
    except XOAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _apply_token_payload(account_id, payload)
    query = urlencode({"connected": "1"})
    return RedirectResponse(f"/accounts/{account_id}/auth/x?{query}", status_code=302)


@app.post("/oauth/x/refresh")
def oauth_x_refresh(account_id: int = Query(..., ge=1)) -> dict[str, str]:
    with SessionLocal() as session:
        token = session.scalar(select(XAuthToken).where(XAuthToken.account_id == account_id))
        if token is None:
            raise HTTPException(status_code=404, detail="x_auth_token_not_found")
        refresh_token = token.refresh_token

    try:
        payload = refresh_access_token(refresh_token=refresh_token)
    except XOAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _apply_token_payload(account_id, payload)
    return {"status": "refreshed"}


@app.get("/oauth/x/status")
def oauth_x_status(account_id: int = Query(..., ge=1)) -> dict[str, object]:
    with SessionLocal() as session:
        token = session.scalar(select(XAuthToken).where(XAuthToken.account_id == account_id))
    if token is None:
        return {"connected": False}
    return {
        "connected": True,
        "expires_at": token.expires_at.isoformat(),
        "scope": token.scope,
        "token_type": token.token_type,
    }
