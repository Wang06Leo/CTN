"""One-off CTN 2026 SDK checks. Explicit --read-live performs one public GET.

No team code, password, login, registration, LLM, post, reply or reporting.
Does NOT call the existing Runner, preventing confusing mock-execution statuses.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys

from ctn.official_readonly import OfficialReadOnlySocialClient
from ctn.selection import select_longest_ascii_question


def offline_demo() -> dict:
    from ctn.models import MockPost

    posts = [
        MockPost("a", "mock1", "Where?"),
        MockPost("b", "mock2", "Could somebody please explain the community opening hours?"),
        MockPost("c", "mock3", "这个句子很长，但只有中文问号，不能作为候选帖子。" * 4 + "？"),
        MockPost("d", "mock4", "This statement is longer but contains no ASCII question mark."),
    ]
    result = select_longest_ascii_question(posts)
    assert result is not None and result.id == "b"
    return {"mode": "offline_demo", "posts": len(posts),
            "selected_post_id": result.id, "selected_length_chars": len(result.text),
            "official_network_calls": 0, "writes_attempted": 0, "status": "passed"}


def check_install() -> dict:
    try:
        import capturethenarrative as sdk
        version = importlib.metadata.version("capturethenarrative")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        return {"mode": "check_install", "installed": False,
                "next_step": "python -m pip install capturethenarrative==0.5.0",
                "error_type": type(exc).__name__}
    return {"mode": "check_install", "installed": True,
            "version": version,
            "2026_version_exact_match": version == "0.5.0",
            "has_legitsocial_class": hasattr(sdk, "LegitSocial"),
            "has_newssites_class": hasattr(sdk, "NewsSites"),
            "has_competitorllm_class": hasattr(sdk, "CompetitorLLM"),
            "official_network_calls": 0, "writes_attempted": 0}


def get_limit_info(limit: object) -> dict | None:
    if limit is None:
        return None
    return {k: getattr(limit, k, None) for k in ("limit", "remaining", "reset")}


def read_once(feed: str, limit: int) -> dict:
    client = OfficialReadOnlySocialClient(feed=feed, limit=limit)
    posts = client.get_feed()
    chosen = select_longest_ascii_question(posts)
    return {
        "mode": "official_readonly", "feed": feed, "requested_limit": limit,
        "received_posts": len(posts),
        "ascii_question_candidates": sum("?" in p.text for p in posts),
        "chosen_post": {"post_id": chosen.id, "length_chars": len(chosen.text)} if chosen else None,
        "rate_limit": get_limit_info(client.rate_limit),
        "official_network_calls": client.read_calls, "writes_attempted": 0,
        "note": "No post contents or credentials printed; SDK may return a blank feed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Official CTN 2026 SDK read-only smoke test")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--offline-demo", action="store_true", help="No SDK, no network")
    actions.add_argument("--check-install", action="store_true", help="Import SDK without network")
    actions.add_argument("--read-live", action="store_true", help="Exactly one public feed read, NO writes")
    parser.add_argument("--feed", choices=("trending", "latest"), default="trending")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    try:
        if args.offline_demo:
            result = offline_demo()
        elif args.check_install:
            result = check_install()
        else:
            result = read_once(args.feed, args.limit)
    except Exception as exc:
        # Show *only local validation error detail*. HTTP bodies can contain
        # private account or post content and must not be printed here.
        result = {"status": "failed", "error_type": type(exc).__name__,
                  "http_status": getattr(exc, "status", None),
                  "retry_after": getattr(exc, "retry_after", None),
                  "writes_attempted": 0}
        if type(exc) is ValueError:
            result["local_validation_error"] = str(exc)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if args.check_install and not result["installed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
