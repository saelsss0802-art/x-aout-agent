from __future__ import annotations

from core.models import Agent, AgentStatus

from apps.worker.feature_toggles import read_bool_toggle, read_float_toggle, read_int_toggle


def _agent(toggles: dict[str, object]) -> Agent:
    return Agent(id=1, account_id=1, status=AgentStatus.active, feature_toggles=toggles)


def test_read_int_toggle_invalid_values_fallback_to_default() -> None:
    agent = _agent(
        {
            "posts_per_day": "abc",
            "x_search_max": -1,
            "web_search_max": 999,
        }
    )

    assert read_int_toggle(agent, "posts_per_day", 2) == 2
    assert read_int_toggle(agent, "x_search_max", 10) == 10
    assert read_int_toggle(agent, "web_search_max", 8) == 8


def test_read_toggle_ignores_non_allowlisted_key() -> None:
    agent = _agent({"unsafe_key": 1, "posting_poll_seconds": 42})

    assert read_int_toggle(agent, "unsafe_key", 7) == 7
    assert read_int_toggle(agent, "posting_poll_seconds", 300) == 42


def test_read_float_and_bool_toggle_are_safe_on_invalid_input() -> None:
    agent = _agent({"posts_per_day": "bad", "web_fetch_max": "not-bool"})

    assert read_float_toggle(agent, "posts_per_day", 1.5, 0.0, 10.0) == 1.5
    assert read_bool_toggle(agent, "web_fetch_max", default=True) is True
