from dataclasses import dataclass


@dataclass(slots=True)
class DomainPlaceholder:
    name: str = "x-aout-core"
