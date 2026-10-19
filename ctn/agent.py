from .models import AgentAction, MockPost
from .selection import select_longest_ascii_question


class RuleBasedAgent:
    """Deterministic demonstration only; not a campaign strategy."""

    def decide(self, posts: list[MockPost]) -> AgentAction:
        # Neutral, fictional, non-political content for verifying the pipeline.
        post = select_longest_ascii_question(posts)
        if post is not None:
            return AgentAction(
                kind="reply",
                post_id=post.id,
                text="Offline test response: thanks for asking. This is simulated content.",
            )
        return AgentAction(kind="noop")
