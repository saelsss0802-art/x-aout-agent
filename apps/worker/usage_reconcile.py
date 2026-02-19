from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from core import UsageReconciler

from .x_usage_client import XUsageClient


def reconcile_app_usage(session: Session, *, usage_date: date) -> dict[str, object]:
    if os.getenv("USE_X_USAGE") != "1":
        return {"x_usage_reconciled": False, "usage_fetch_failed": False}

    token = os.getenv("X_BEARER_TOKEN")
    if not token:
        return {
            "x_usage_reconciled": False,
            "usage_fetch_failed": True,
            "usage_error": "missing_x_bearer_token",
        }

    unit_price = Decimal(os.getenv("X_UNIT_PRICE")) if os.getenv("X_UNIT_PRICE") else None
    reconciler = UsageReconciler(session, app_agent_id=0, unit_price=unit_price)
    usage_client = XUsageClient(bearer_token=token)
    snapshot = usage_client.fetch_daily_usage(usage_date)
    reconciler.reconcile_x_usage(target_date=usage_date, units=snapshot.units, raw=snapshot.raw)

    return {
        "x_usage_reconciled": True,
        "x_usage_units": snapshot.units,
        "usage_fetch_failed": False,
    }
