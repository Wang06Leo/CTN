"""Deliberately disabled real adapter: never opens a socket.

Replace only after explicit 2026 documentation review, organiser approval where
required, and contract/integration testing. No endpoints or authentication are guessed.
"""
from .models import AgentAction, MockPost

class LiveIntegrationBlocked(RuntimeError):
    pass

class OfficialSocialClient:
    def __init__(self, *args: object, **kwargs: object):
        raise LiveIntegrationBlocked(
            "LIVE DISABLED: official 2026 API contract, authentication, "
            "rate limits, account limits and permission checks are unverified."
        )

    def get_feed(self) -> list[MockPost]:
        raise LiveIntegrationBlocked("LIVE DISABLED")

    def execute(self, action: AgentAction) -> dict[str, str]:
        raise LiveIntegrationBlocked("LIVE DISABLED")
