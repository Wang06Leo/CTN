"""v0.5 run: local/official feed -> longest ASCII '?' -> optional question classification.

NO social account login and NO social action calls.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from ctn.integrated_readonly import run_readonly_cycles
from ctn.official_readonly import OfficialReadOnlySocialClient
from ctn.replay_client import ReplaySocialClient
from ctn.review_state import ReviewStore
from ctn.rule_policy import OfflineRulePolicy
from ctn.question_classifier import (
    FixtureQuestionProvider, OfficialQuestionProvider, QuestionClassifier
)


def main() -> int:
    ap = argparse.ArgumentParser(description='CTN v0.5: candidate review, optional official model classification, never post')
    ap.add_argument('--source', choices=('mock', 'official'), default='mock')
    ap.add_argument('--feed', choices=('trending', 'latest'), default='trending')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--model', choices=('none', 'fixture', 'official'), default='none')
    ap.add_argument('--confirm-public-read', action='store_true')
    ap.add_argument('--confirm-official-llm', action='store_true')
    ap.add_argument('--record-reviewed', action='store_true')
    ap.add_argument('--fixture', type=Path, default=Path('fixtures/length_priority_demo.json'))
    ap.add_argument('--db', type=Path, default=Path('data/v05_review_state.sqlite3'))
    ap.add_argument('--log', type=Path, default=Path('logs/v05_readonly.jsonl'))
    ap.add_argument('--stop-path', type=Path, default=Path('STOP'))
    ap.add_argument('--policy', type=Path, default=Path('config/offline_policy.json'))
    args = ap.parse_args()

    if args.source == 'official' and not args.confirm_public_read:
        ap.error('Official feed requires --confirm-public-read')
    if not 1 <= args.limit <= 10:
        ap.error('v0.5 keeps a conservative single-page limit of 1..10 posts')
    if args.model == 'official':
        if args.source != 'official' or not args.confirm_official_llm:
            ap.error('Official model requires --source official and --confirm-official-llm')
        if not os.environ.get('CTN_TEAM_CODE', '').strip():
            ap.error('Set CTN_TEAM_CODE as a local environment variable; do NOT pass it on the command line')

    classifier = None
    if args.model == 'fixture':
        classifier = QuestionClassifier(FixtureQuestionProvider())
    elif args.model == 'official':
        classifier = QuestionClassifier(OfficialQuestionProvider(os.environ['CTN_TEAM_CODE']))

    # Instantiate any network client only after all CLI checks.
    client = (OfficialReadOnlySocialClient(feed=args.feed, limit=args.limit)
              if args.source == 'official' else ReplaySocialClient.from_file(args.fixture))
    policy = OfflineRulePolicy.from_file(args.policy)
    with ReviewStore(args.db) as store:
        summary = run_readonly_cycles(
            client, store, args.log,
            source=args.feed if args.source == 'official' else 'local_fixture',
            cycles=1, interval_seconds=65.0, record_reviewed=args.record_reviewed,
            official=(args.source == 'official'), stop_path=args.stop_path,
            policy=policy, classifier=classifier,
        )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if summary['stop_reason'] == 'read_or_validation_failed' or 'read_or_validation_failed' in summary['status_counts'] else 0


if __name__ == '__main__':
    sys.exit(main())
