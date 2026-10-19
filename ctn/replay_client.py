"""Offline replay of locally created, NORMALIZED mock fixtures.

Do not copy private competition dumps into fixtures until organiser's data
handling and third-party tool permissions have been confirmed.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from .models import AgentAction, MockPost

@dataclass
class ReplaySocialClient:
    posts: list[MockPost]
    executed: list[AgentAction] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> "ReplaySocialClient":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("posts"), list):
            raise ValueError("Fixture must contain 'posts' as a list")
        posts: list[MockPost] = []
        for item in raw["posts"]:
            if not isinstance(item, dict):
                raise ValueError("Post item must be an object")
            if any(not isinstance(item.get(k), str) for k in ("id", "author", "text")):
                raise ValueError("Post id, author and text must be strings")
            if not item["id"]:
                raise ValueError("Post id must not be empty")
            posts.append(MockPost(**{k: item[k] for k in ("id", "author", "text")}))
        return cls(posts)

    def get_feed(self) -> list[MockPost]:
        return list(self.posts)

    def execute(self, action: AgentAction) -> dict[str, str]:
        self.executed.append(action)
        return {"mock_action_id": f"replay-{len(self.executed)}", "status": "mock_only"}
