from __future__ import annotations

from typing import Protocol


class WorkerJob(Protocol):
    def __call__(self) -> None: ...
