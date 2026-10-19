"""v0.4.2 command line entry: offline or explicitly opted-in public feed GET.

NO login, account operations, model calls, writes, or real action execution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ctn.integrated_readonly import run_readonly_cycles
from ctn.official_readonly import OfficialReadOnlySocialClient
from ctn.replay_client import ReplaySocialClient
from ctn.review_state import ReviewStore
from ctn.rule_policy import OfflineRulePolicy


def main() -> int:
    parser = argparse.ArgumentParser(description='CTN v0.4.2 READ-ONLY integrated Agent')
    parser.add_argument('--source', choices=('mock', 'official'), default='mock')
    parser.add_argument('--confirm-public-read', action='store_true',
                        help='Explicitly allow GET access to official public feed')
    parser.add_argument('--feed', choices=('trending', 'latest'), default='trending')
    parser.add_argument('--limit', type=int, default=10,
                        help='total records per cycle; official multi-cycle requires <=10')
    parser.add_argument('--cycles', type=int, default=1)
    parser.add_argument('--interval-seconds', type=float, default=65.0)
    parser.add_argument('--record-reviewed', action='store_true',
                        help='store locally reviewed post IDs, NEVER as replied-to')
    parser.add_argument('--fixture', type=Path, default=Path('fixtures/length_priority_demo.json'))
    parser.add_argument('--db', type=Path, default=Path('data/review_state.sqlite3'))
    parser.add_argument('--log', type=Path, default=Path('logs/readonly_events.jsonl'))
    parser.add_argument('--policy', type=Path, default=Path('config/offline_policy.json'))
    parser.add_argument('--stop-path', type=Path, default=Path('STOP'))
    args = parser.parse_args()

    if args.source == 'official' and not args.confirm_public_read:
        parser.error('official read requires --confirm-public-read')
    if args.source == 'official' and args.cycles > 1 and args.limit > 10:
        parser.error('official multiple cycles require --limit <= 10 (conservative pacing)')
    if type(args.limit) is not int or not 1 <= args.limit <= 30:
        parser.error('limit must be an integer from 1 to 30 (local cap)')

    if args.source == 'official':
        client = OfficialReadOnlySocialClient(feed=args.feed, limit=args.limit)
    else:
        client = ReplaySocialClient.from_file(args.fixture)
    policy = OfflineRulePolicy.from_file(args.policy)
    with ReviewStore(args.db) as store:
        report = run_readonly_cycles(
            client, store, args.log, source=args.feed if args.source == 'official' else 'local_fixture',
            cycles=args.cycles, interval_seconds=args.interval_seconds,
            record_reviewed=args.record_reviewed,
            official=(args.source == 'official'), stop_path=args.stop_path,
            policy=policy,
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report['stop_reason'] == 'read_or_validation_failed' else 0


if __name__ == '__main__':
    sys.exit(main())
