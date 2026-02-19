from __future__ import annotations

import json
from typing import Any

from core.models import Agent

_ALLOWED_TOGGLE_RULES: dict[str, tuple[str, float | None, float | None]] = {
    "posts_per_day": ("int", 0, 20),
    "x_search_max": ("int", 0, 50),
    "web_search_max": ("int", 0, 50),
    "web_fetch_max": ("int", 0, 20),
    "posting_poll_seconds": ("int", 1, 86_400),
    "reply_quote_daily_max": ("int", 0, 100),
}


def _toggle_log(agent: Agent, *, key: str, reason: str, default: Any, value: Any | None = None) -> None:
    raw_repr = None if value is None else repr(value)
    payload = {
        "event": "feature_toggle_fallback",
        "agent_id": agent.id,
        "key": key,
        "reason": reason,
        "default": default,
        "raw": raw_repr[:64] if isinstance(raw_repr, str) else raw_repr,
    }
    print(json.dumps(payload, ensure_ascii=True))


def _read_toggle(agent: Agent, key: str) -> Any:
    if key not in _ALLOWED_TOGGLE_RULES:
        return None
    toggles = agent.feature_toggles if isinstance(agent.feature_toggles, dict) else {}
    return toggles.get(key)


def read_int_toggle(agent: Agent, key: str, default: int) -> int:
    rule = _ALLOWED_TOGGLE_RULES.get(key)
    if rule is None:
        _toggle_log(agent, key=key, reason="key_not_allowlisted", default=default)
        return default

    raw = _read_toggle(agent, key)
    if raw is None:
        return default

    if isinstance(raw, bool):
        _toggle_log(agent, key=key, reason="invalid_int", default=default, value=raw)
        return default

    try:
        value = int(raw)
    except (TypeError, ValueError):
        _toggle_log(agent, key=key, reason="invalid_int", default=default, value=raw)
        return default

    min_value = int(rule[1]) if rule[1] is not None else None
    max_value = int(rule[2]) if rule[2] is not None else None
    if min_value is not None and value < min_value:
        _toggle_log(agent, key=key, reason="out_of_range", default=default, value=raw)
        return default
    if max_value is not None and value > max_value:
        _toggle_log(agent, key=key, reason="out_of_range", default=default, value=raw)
        return default

    return value


def read_float_toggle(agent: Agent, key: str, default: float, min_value: float, max_value: float) -> float:
    if key not in _ALLOWED_TOGGLE_RULES:
        _toggle_log(agent, key=key, reason="key_not_allowlisted", default=default)
        return default

    raw = _read_toggle(agent, key)
    if raw is None:
        return default

    try:
        value = float(raw)
    except (TypeError, ValueError):
        _toggle_log(agent, key=key, reason="invalid_float", default=default, value=raw)
        return default

    if value < min_value or value > max_value:
        _toggle_log(agent, key=key, reason="out_of_range", default=default, value=raw)
        return default

    return value


def read_bool_toggle(agent: Agent, key: str, default: bool) -> bool:
    if key not in _ALLOWED_TOGGLE_RULES:
        _toggle_log(agent, key=key, reason="key_not_allowlisted", default=default)
        return default

    raw = _read_toggle(agent, key)
    if raw is None:
        return default

    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False

    _toggle_log(agent, key=key, reason="invalid_bool", default=default, value=raw)
    return default
