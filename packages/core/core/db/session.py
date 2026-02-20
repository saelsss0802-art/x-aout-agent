from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

ERROR_DATABASE_URL_REQUIRED = "DATABASE_URL is required"


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(ERROR_DATABASE_URL_REQUIRED)
    return database_url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_database_url(), future=True)


@lru_cache(maxsize=1)
def _get_sessionmaker() -> sessionmaker:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)


class _LazyEngine:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_engine(), name)


class _LazySessionLocal:
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _get_sessionmaker()(*args, **kwargs)


engine = _LazyEngine()
SessionLocal = _LazySessionLocal()
