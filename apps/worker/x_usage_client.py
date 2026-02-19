from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import httpx


class XUsageClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageSnapshot:
    units: int
    raw: dict[str, object]


class XUsageClient:
    def __init__(
        self,
        *,
        bearer_token: str,
        base_url: str = "https://api.x.com/2",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._bearer_token = bearer_token
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client or httpx.Client(timeout=15.0)

    def fetch_daily_usage(self, target_date: date) -> UsageSnapshot:
        start_time = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
        end_time = start_time + timedelta(days=1)
        params = {
            "start_time": start_time.isoformat().replace("+00:00", "Z"),
            "end_time": end_time.isoformat().replace("+00:00", "Z"),
        }
        try:
            response = self._http_client.get(
                f"{self._base_url}/usage/tweets",
                headers={"Authorization": f"Bearer {self._bearer_token}", "Accept": "application/json"},
                params=params,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise XUsageClientError(f"usage_api_status_{exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise XUsageClientError(f"usage_api_network_{exc.__class__.__name__}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            payload = {}
        return UsageSnapshot(units=self._extract_usage_units(payload), raw=payload)

    def _extract_usage_units(self, payload: dict[str, object]) -> int:
        data = payload.get("data")
        if isinstance(data, list):
            return sum(self._int_metric(item.get("usage")) for item in data if isinstance(item, dict))
        if isinstance(data, dict):
            if isinstance(data.get("usage"), (int, float, str)):
                return self._int_metric(data.get("usage"))
            totals = data.get("totals")
            if isinstance(totals, dict):
                return self._int_metric(totals.get("usage"))
        return 0

    @staticmethod
    def _int_metric(value: object) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
