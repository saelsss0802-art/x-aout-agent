from __future__ import annotations

import argparse
from datetime import date

from .daily_routine import run_daily_confirmed_routine


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily confirmed-metrics routine once")
    parser.add_argument("--agent-id", type=int, required=True)
    parser.add_argument("--date", type=str, required=False, help="Base date YYYY-MM-DD")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    base_date = date.fromisoformat(args.date) if args.date else None
    result = run_daily_confirmed_routine(agent_id=args.agent_id, base_date=base_date)
    print(result)


if __name__ == "__main__":
    main()
