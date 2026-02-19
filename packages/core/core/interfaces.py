from __future__ import annotations

from typing import Protocol


class WorkerJob(Protocol):
    def __call__(self) -> None: ...


class Poster(Protocol):
    def post_text(self, agent_id: int, text: str) -> str: ...

    def post_thread(self, agent_id: int, parts: list[str]) -> str: ...

    def post_reply(self, agent_id: int, target_post_url: str, text: str) -> str: ...

    def post_quote_rt(self, agent_id: int, target_post_url: str, text: str) -> str: ...
