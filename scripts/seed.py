from __future__ import annotations

from sqlalchemy import text

from apps.api.app.db import engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO heartbeat (source) VALUES ('seed')"))
    print("seed completed")


if __name__ == "__main__":
    main()
