from dataclasses import dataclass, field
from .models import AgentAction, MockPost


@dataclass
class MockSocialClient:
    """In-memory stand-in. It performs no HTTP requests and has no credentials."""
    posts: list[MockPost]
    fail_execute: bool = False
    executed: list[AgentAction] = field(default_factory=list)

    def get_feed(self) -> list[MockPost]:
        return list(self.posts)

    def execute(self, action: AgentAction) -> dict[str, str]:
        if self.fail_execute:
            raise ConnectionError("Simulated backend failure: no network was used")
        self.executed.append(action)
        return {"mock_action_id": f"mock-{len(self.executed)}", "status": "accepted"}
