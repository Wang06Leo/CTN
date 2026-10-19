"""Deterministic offline post selection, not a score/engagement predictor.

Eligible: text contains the ASCII question mark '?'. Among eligible posts,
prefer the highest Python character count (len(text)); ties keep the first
post in feed order. Full-width Chinese '？' is NOT a trigger.
"""
from __future__ import annotations

from .models import MockPost


def select_longest_ascii_question(posts: list[MockPost]) -> MockPost | None:
    eligible = (post for post in posts if "?" in post.text)
    return max(eligible, key=lambda post: len(post.text), default=None)
