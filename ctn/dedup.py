"""In-process local duplicate prevention; not durable across restarts."""
from dataclasses import dataclass, field
import hashlib
from .models import AgentAction

@dataclass
class InMemoryDeduper:
    completed: set[tuple[str, str, str]] = field(default_factory=set)

    @staticmethod
    def key(action: AgentAction) -> tuple[str, str, str]:
        return (action.kind, action.post_id or "", hashlib.sha256((action.text or "").encode("utf-8")).hexdigest())

    def seen(self, action: AgentAction) -> bool:
        return self.key(action) in self.completed

    def record_completed(self, action: AgentAction) -> None:
        self.completed.add(self.key(action))
