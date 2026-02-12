from __future__ import annotations

import argparse
from datetime import date

from .daily_routine import run_daily_routine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run worker daily routine once")
    parser.add_argument("--agent-id", type=int, required=True)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_daily_routine(agent_id=args.agent_id, base_date=args.date)
    print(
        f"run_once completed agent_id={args.agent_id} target_date={result['target_date']} log={result['log_path']}"
    )


if __name__ == "__main__":
    main()
