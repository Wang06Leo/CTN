"""CTN 2026 SDK integration: public feeds ONLY. Never authenticates or writes.

This adapter deliberately does not implement any working write operation.
SDK documented on https://pypi.org/project/capturethenarrative/ (v0.5.0).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import AgentAction, MockPost


class OfficialSchemaMismatch(ValueError):
    """The actual SDK's post fields do not match the documented contract."""


class OfficialWriteBlocked(RuntimeError):
    """Protect read-only experiments from an accidental posting action."""


def normalize_public_post(post: Mapping[str, Any]) -> MockPost:
    """Map documented 2026 fields to the existing internal v0.3.1 type."""
    if not isinstance(post, Mapping):
        raise OfficialSchemaMismatch("post must be a mapping")
    ident = post.get("post_id")
    author = post.get("author_id")
    body = post.get("body")
    if isinstance(ident, bool) or not isinstance(ident, (int, str)) or not str(ident):
        raise OfficialSchemaMismatch("post_id is missing or invalid")
    if isinstance(author, bool) or not isinstance(author, (int, str)) or not str(author):
        raise OfficialSchemaMismatch("author_id is missing or invalid")
    if body is not None and not isinstance(body, str):
        raise OfficialSchemaMismatch("body must be a string or null")
    return MockPost(id=str(ident), author=str(author), text=body or "")


class OfficialReadOnlySocialClient:
    """Public feed pagination ONLY; all writes are blocked.

    A user-facing `limit` is the maximum TOTAL records to collect, not
    necessarily one API request. Uses <=10 items per page as in the official
    Getting Started example, and follows the SDK's `Page.next_cursor`.

    The local total <=50 is a defensive testing cap, NOT a CTN API rule.
    `social` may be injected to test with no SDK/network.
    """

    def __init__(self, *, social: Any = None, limit: int = 10, feed: str = "trending",
                 before_page=None, after_page=None) -> None:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("total limit must be an integer from 1 to 50 (local safety cap)")
        if feed not in ("trending", "latest"):
            raise ValueError("feed must be trending or latest")
        self._social = social
        self.limit = limit
        self.feed = feed
        self.read_calls = 0
        self.before_page = before_page
        self.after_page = after_page

    def _get_social(self) -> Any:
        if self._social is None:
            try:
                import capturethenarrative as sdk
            except ImportError as exc:
                raise RuntimeError("Install official SDK: pip install capturethenarrative==0.5.0") from exc
            self._social = sdk.LegitSocial(timeout=10.0, max_retries=0)
        return self._social

    def get_feed(self) -> list[MockPost]:
        self.read_calls = 0
        social = self._get_social()
        method = social.feed_trending if self.feed == "trending" else social.feed_latest
        posts: list[MockPost] = []
        cursor: Any = None
        previous_cursors: set[str] = set()
        while len(posts) < self.limit:
            # 10 is the per-page size used in the official Getting Started.
            page_size = min(10, self.limit - len(posts))
            kwargs = {"limit": page_size}
            if cursor is not None:
                kwargs["cursor"] = cursor
            if self.before_page is not None:
                self.before_page()
            self.read_calls += 1
            page = method(**kwargs)
            if self.after_page is not None:
                self.after_page(self.rate_limit)
            if not isinstance(page, list) or len(page) > page_size:
                raise OfficialSchemaMismatch('Invalid feed page')
            parsed = [normalize_public_post(p) for p in page]
            posts.extend(parsed[:page_size])

            # Plain lists have no next_cursor, so this remains compatible
            # with older SDK fakes and non-paginated test fixtures.
            next_cursor = getattr(page, "next_cursor", None)
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise OfficialSchemaMismatch('Invalid pagination cursor')
            if not next_cursor or not parsed:
                break
            marker = repr(next_cursor)
            if marker in previous_cursors:
                raise OfficialSchemaMismatch("Feed repeated the same pagination cursor")
            previous_cursors.add(marker)
            cursor = next_cursor
        return posts

    @property
    def rate_limit(self) -> Any:
        return getattr(self._social, "rate_limit", None)

    def execute(self, action: AgentAction) -> dict[str, str]:
        # Existing Runner is NOT approved for real-data execution.
        raise OfficialWriteBlocked("OFFICIAL WRITE DISABLED: read-only connector")
