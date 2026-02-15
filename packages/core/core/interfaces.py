from __future__ import annotations

from typing import Protocol


class WorkerJob(Protocol):
    def __call__(self) -> None: ...


class Poster(Protocol):
    def post_text(self, agent_id: int, text: str) -> str: ...
