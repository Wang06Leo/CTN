"""Internal adapter contract. NOT derived from any official CTN schema."""
from typing import Protocol
from .models import AgentAction, MockPost

class SocialClient(Protocol):
    def get_feed(self) -> list[MockPost]: ...
    def execute(self, action: AgentAction) -> dict[str, str]: ...
