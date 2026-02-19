from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select

from core.models import Account, Agent, AgentStatus, AuditLog, CostLog, DailyPDCA, OAuthState, XAuthToken

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


class StopRequest(BaseModel):
    reason: str
    until: datetime | None = None


class AgentUpdateRequest(BaseModel):
    daily_budget: int | None = None
    feature_toggles: dict[str, Any] | None = None


def _cost_to_dict(cost: CostLog | None) -> dict[str, float | int | None]:
    if cost is None:
        return {
            "x_api_cost_estimate": 0.0,
            "llm_cost": 0.0,
            "total": 0.0,
            "x_usage_units": None,
            "x_api_cost_actual": None,
        }
    return {
        "x_api_cost_estimate": float(Decimal(cost.x_api_cost_estimate)),
        "llm_cost": float(Decimal(cost.llm_cost)),
        "total": float(Decimal(cost.total)),
        "x_usage_units": cost.x_usage_units,
        "x_api_cost_actual": float(Decimal(cost.x_api_cost_actual)) if cost.x_api_cost_actual is not None else None,
    }


def _agent_to_dict(agent: Agent) -> dict[str, object]:
    return {
        "id": agent.id,
        "account_id": agent.account_id,
        "status": agent.status.value,
        "stop_reason": agent.stop_reason,
        "stopped_at": agent.stopped_at.isoformat() if agent.stopped_at else None,
        "stop_until": agent.stop_until.isoformat() if agent.stop_until else None,
        "daily_budget": agent.daily_budget,
    }


def _defaults_payload() -> dict[str, int]:
    return {
        "reply_quote_daily_max": 3,
        "posts_per_day_default": 1,
        "search_max_default": 10,
        "fetch_max_default": 3,
        "split_x": 100,
        "split_llm": 200,
        "total": 300,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/agents")
def list_agents() -> dict[str, object]:
    today = date.today()
    with SessionLocal() as session:
        agents = session.scalars(select(Agent).order_by(Agent.id.asc())).all()
        rows: list[dict[str, object]] = []
        for agent in agents:
            cost = session.scalar(select(CostLog).where(CostLog.agent_id == agent.id, CostLog.date == today))
            latest_pdca = session.scalar(
                select(DailyPDCA).where(DailyPDCA.agent_id == agent.id).order_by(DailyPDCA.date.desc()).limit(1)
            )
            row = _agent_to_dict(agent)
            row["today_cost"] = _cost_to_dict(cost)
            row["latest_pdca_date"] = latest_pdca.date.isoformat() if latest_pdca else None
            rows.append(row)

        app_wide_cost = session.scalar(select(CostLog).where(CostLog.agent_id == 0, CostLog.date == today))

    return {
        "date": today.isoformat(),
        "app_wide_usage": {
            "x_usage_units": app_wide_cost.x_usage_units if app_wide_cost else None,
            "x_api_cost_actual": float(Decimal(app_wide_cost.x_api_cost_actual)) if app_wide_cost and app_wide_cost.x_api_cost_actual is not None else None,
        },
        "agents": rows,
    }


@app.get("/api/agents/{agent_id}")
def get_agent(agent_id: int) -> dict[str, object]:
    with SessionLocal() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent_not_found")

        pdca_rows = session.scalars(
            select(DailyPDCA).where(DailyPDCA.agent_id == agent_id).order_by(DailyPDCA.date.desc()).limit(7)
        ).all()

    result = _agent_to_dict(agent)
    result["feature_toggles"] = agent.feature_toggles or {}
    result["daily_pdca"] = [
        {
            "date": row.date.isoformat(),
            "analytics_summary": row.analytics_summary or {},
        }
        for row in pdca_rows
    ]
    return result


@app.patch("/api/agents/{agent_id}")
def patch_agent(agent_id: int, payload: AgentUpdateRequest) -> dict[str, object]:
    patch: dict[str, Any] = {}
    if payload.daily_budget is not None:
        if payload.daily_budget < 0:
            raise HTTPException(status_code=400, detail="daily_budget_invalid")
        patch["daily_budget"] = payload.daily_budget
    if payload.feature_toggles is not None:
        patch["feature_toggles"] = payload.feature_toggles
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent_not_found")

        diff: dict[str, Any] = {}
        try:
            if "daily_budget" in patch and agent.daily_budget != patch["daily_budget"]:
                agent.daily_budget = patch["daily_budget"]
                diff["daily_budget"] = patch["daily_budget"]

            if "feature_toggles" in patch:
                current_toggles = dict(agent.feature_toggles or {})
                for key, value in patch["feature_toggles"].items():
                    if current_toggles.get(key) != value:
                        diff[key] = value
                    current_toggles[key] = value
                agent.feature_toggles = current_toggles

            session.add(
                AuditLog(
                    agent_id=agent_id,
                    date=now.date(),
                    source="dashboard",
                    event_type="agent_update",
                    status="success",
                    reason=None,
                    payload_json=diff,
                )
            )
            session.commit()
            session.refresh(agent)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            session.add(
                AuditLog(
                    agent_id=agent_id,
                    date=now.date(),
                    source="dashboard",
                    event_type="agent_update",
                    status="failed",
                    reason=type(exc).__name__,
                    payload_json=patch,
                )
            )
            session.commit()
            raise

    result = _agent_to_dict(agent)
    result["feature_toggles"] = agent.feature_toggles or {}
    return result


@app.get("/api/config/defaults")
def get_config_defaults() -> dict[str, int]:
    return _defaults_payload()


@app.get("/api/agents/{agent_id}/audit")
def get_agent_audit(agent_id: int, limit: int = Query(20, ge=1, le=200)) -> dict[str, object]:
    with SessionLocal() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent_not_found")
        rows = session.scalars(
            select(AuditLog).where(AuditLog.agent_id == agent_id).order_by(AuditLog.created_at.desc()).limit(limit)
        ).all()

    return {
        "agent_id": agent_id,
        "items": [
            {
                "date": row.date.isoformat(),
                "source": row.source,
                "event_type": row.event_type,
                "status": row.status,
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@app.post("/api/agents/{agent_id}/stop")
def stop_agent(agent_id: int, payload: StopRequest) -> dict[str, str]:
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason_required")

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent_not_found")

        stop_until = payload.until
        if stop_until is not None and stop_until.tzinfo is None:
            stop_until = stop_until.replace(tzinfo=timezone.utc)

        agent.status = AgentStatus.stopped
        agent.stop_reason = reason
        agent.stopped_at = now
        agent.stop_until = stop_until

        session.add(
            AuditLog(
                agent_id=agent_id,
                date=now.date(),
                source="dashboard",
                event_type="manual_stop",
                status="success",
                reason=reason,
                payload_json={"until": stop_until.isoformat() if stop_until else None},
            )
        )
        session.commit()

    return {"status": "stopped"}


@app.post("/api/agents/{agent_id}/resume")
def resume_agent(agent_id: int) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent_not_found")

        agent.status = AgentStatus.active
        agent.stop_reason = None
        agent.stopped_at = None
        agent.stop_until = None

        session.add(
            AuditLog(
                agent_id=agent_id,
                date=now.date(),
                source="dashboard",
                event_type="manual_resume",
                status="success",
                reason=None,
                payload_json={},
            )
        )
        session.commit()

    return {"status": "active"}


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
