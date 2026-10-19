"""CTN v0.5.1: read-only Feed + candidate selection + optional official LLM.

No social login. No post/reply/like/report/follow API calls.
Only explicit --confirm-public-read and --confirm-official-llm allow calls.
A Team Code is read only when requested; never printed or passed in argv.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ctn.integrated_readonly import run_readonly_cycles
from ctn.local_secrets import load_team_code, MissingTeamCode, LocalSecretsError
from ctn.official_readonly import OfficialReadOnlySocialClient
from ctn.question_classifier import FixtureQuestionProvider, OfficialQuestionProvider, QuestionClassifier
from ctn.replay_client import ReplaySocialClient
from ctn.review_state import ReviewStore
from ctn.rule_policy import OfflineRulePolicy

PROJECT_DIR = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description='CTN v0.5.1: official feed + official LLM, read-only')
    ap.add_argument('--check-config', action='store_true', help='Check local Team Code availability without network calls')
    ap.add_argument('--source', choices=('mock', 'official'), default='mock')
    ap.add_argument('--feed', choices=('trending', 'latest'), default='trending')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--model', choices=('none', 'fixture', 'official'), default='none')
    ap.add_argument('--fixture-model-result', choices=('true', 'false'), default='true')
    ap.add_argument('--confirm-public-read', action='store_true')
    ap.add_argument('--confirm-official-llm', action='store_true')
    ap.add_argument('--record-reviewed', action='store_true')
    ap.add_argument('--fixture', type=Path, default=PROJECT_DIR / 'fixtures/length_priority_demo.json')
    ap.add_argument('--db', type=Path, default=PROJECT_DIR / 'data/v051_review_state.sqlite3')
    ap.add_argument('--log', type=Path, default=PROJECT_DIR / 'logs/v051_readonly.jsonl')
    ap.add_argument('--stop-path', type=Path, default=PROJECT_DIR / 'STOP')
    ap.add_argument('--policy', type=Path, default=PROJECT_DIR / 'config/offline_policy.json')
    # Do not accept a secret value via any CLI flag.
    return ap


def execute(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check_config:
        try:
            _, source = load_team_code(PROJECT_DIR / '.env')
        except (MissingTeamCode, LocalSecretsError):
            print(json.dumps({'configured': False, 'source': None,
                              'network_calls': 0, 'writes_attempted': 0}, indent=2))
            return 1
        print(json.dumps({'configured': True, 'source': source,
                          'network_calls': 0, 'writes_attempted': 0}, indent=2))
        return 0

    if args.source == 'official' and not args.confirm_public_read:
        parser.error('Official social feed requires --confirm-public-read')
    if type(args.limit) is not int or not 1 <= args.limit <= 10:
        parser.error('v0.5.1 limits each live feed observation to one page of 1–10 posts')
    if args.model == 'official' and not args.confirm_official_llm:
        parser.error('Official LLM requires --confirm-official-llm (consumes shared team allowance)')
    if args.model != 'fixture' and args.fixture_model_result != 'true':
        parser.error('--fixture-model-result is only used with --model fixture')

    classifier = None
    if args.model == 'fixture':
        fixture_response = '{"is_question": ' + args.fixture_model_result + '}'
        classifier = QuestionClassifier(FixtureQuestionProvider(fixture_response))
    elif args.model == 'official':
        try:
            code, _ = load_team_code(PROJECT_DIR / '.env')
        except (MissingTeamCode, LocalSecretsError):
            # Never print a secret or an exception message containing one.
            print(json.dumps({'status': 'missing_or_invalid_team_code', 'writes_attempted': 0}))
            return 2
        classifier = QuestionClassifier(OfficialQuestionProvider(code))
        del code

    client = (OfficialReadOnlySocialClient(feed=args.feed, limit=args.limit)
              if args.source == 'official' else ReplaySocialClient.from_file(args.fixture))
    policy = OfflineRulePolicy.from_file(args.policy)
    with ReviewStore(args.db) as store:
        summary = run_readonly_cycles(
            client, store, args.log,
            source=args.feed if args.source == 'official' else 'local_fixture',
            cycles=1, interval_seconds=65.0,
            record_reviewed=args.record_reviewed,
            official=(args.source == 'official'),
            stop_path=args.stop_path, policy=policy, classifier=classifier,
        )
    summary['model_mode'] = args.model
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary['stop_reason'] in ('read_or_validation_failed', 'local_read_cooldown', 'local_model_cooldown'):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(execute())
