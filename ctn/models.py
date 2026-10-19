from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MockPost:
    id: str
    author: str
    text: str


@dataclass(frozen=True)
class AgentAction:
    kind: Literal["reply", "noop"]
    post_id: str | None = None
    text: str | None = None
