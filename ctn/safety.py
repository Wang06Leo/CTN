from collections import deque
from dataclasses import dataclass, field
from typing import Callable
import time
from .models import AgentAction, MockPost


class ValidationError(ValueError):
    pass


@dataclass
class ActionValidator:
    max_chars: int = 200  # LOCAL MOCK SETTING, not an official platform limit

    def validate(self, action: AgentAction, posts: list[MockPost]) -> None:
        if action.kind != "reply":
            raise ValidationError(f"Not an executable action: {action.kind!r}")
        if not action.post_id or action.post_id not in {p.id for p in posts}:
            raise ValidationError("Unknown or missing local post_id")
        if not action.text or not action.text.strip():
            raise ValidationError("Reply text must be non-empty")
        if len(action.text) > self.max_chars:
            raise ValidationError("Reply exceeds local mock text cap")


@dataclass
class SlidingWindowLimiter:
    """Local placeholder, NOT the official rate limit."""
    maximum: int = 2
    window_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    events: deque[float] = field(default_factory=deque)

    def permit(self) -> bool:
        now = self.clock()
        while self.events and now - self.events[0] >= self.window_seconds:
            self.events.popleft()
        if len(self.events) >= self.maximum:
            return False
        self.events.append(now)
        return True
