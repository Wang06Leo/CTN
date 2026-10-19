"""v0.5.2: offline by default, explicitly enabled official reads/classification."""
import json
from pathlib import Path
import sys

from run_v051 import build_parser, PROJECT_DIR
from ctn.local_secrets import load_team_code
from ctn.official_readonly import OfficialReadOnlySocialClient
from ctn.question_classifier import QuestionClassifier, FixtureQuestionProvider, OfficialQuestionProvider
from ctn.replay_client import ReplaySocialClient
from ctn.reliability_state import ReliabilityStore
from ctn.reliable_readonly import run_cycle
from ctn.rule_policy import OfflineRulePolicy


class LazyOfficialProvider:
    is_official = True

    def prepare(self):
        # No credential read or SDK initialization for an empty candidate queue.
        code, _ = load_team_code(PROJECT_DIR / '.env')
        self.provider = OfficialQuestionProvider(code)

    def ask_classification(self, text):
        provider = self.provider
        try:
            return provider.ask_classification(text)
        finally:
            provider._llm.close()


def execute(argv=None):
    parser = build_parser()
    parser.description = 'CTN v0.5.2: reliable read-only candidate queue'
    parser.set_defaults(db=PROJECT_DIR / 'data/v052_review_state.sqlite3',
                        log=PROJECT_DIR / 'logs/v052_readonly.jsonl', model='fixture')
    parser.add_argument('--cycles', type=int, default=1, help='1..500 offline; official feed or LLM: exactly 1')
    args = parser.parse_args(argv)
    if args.source == 'official' and not args.confirm_public_read:
        parser.error('Official Feed requires --confirm-public-read')
    if args.model == 'official' and not args.confirm_official_llm:
        parser.error('Official LLM requires --confirm-official-llm')
    if not 1 <= args.cycles <= 500:
        parser.error('--cycles must be 1..500')
    if (args.source == 'official' or args.model == 'official') and args.cycles != 1:
        parser.error('Official operations allow one cycle per invocation')
    if not 1 <= args.limit <= 10:
        parser.error('Official workflow uses one page of 1..10 posts')
    if args.model != 'fixture' and args.fixture_model_result != 'true':
        parser.error('--fixture-model-result requires --model fixture')
    if args.check_config:
        from run_v051 import execute as legacy_execute
        return legacy_execute(['--check-config'])
    try:
        official = args.source == 'official'
        client = (OfficialReadOnlySocialClient(feed=args.feed, limit=args.limit) if official
                  else ReplaySocialClient.from_file(args.fixture))
        classifier = None
        if args.model == 'fixture':
            classifier = QuestionClassifier(FixtureQuestionProvider('{"is_question": ' + args.fixture_model_result + '}'))
        elif args.model == 'official':
            classifier = QuestionClassifier(LazyOfficialProvider())
        policy = OfflineRulePolicy.from_file(args.policy)
        outcomes = []
        with ReliabilityStore(args.db) as store:
            for _ in range(args.cycles):
                result = run_cycle(client, store, args.log, official=official,
                                   classifier=classifier, stop_path=args.stop_path, policy=policy)
                outcomes.append(result)
                if result['status'] not in ('proposed', 'classified_not_question', 'noop'):
                    break
        from collections import Counter
        summary = dict(version='0.5.2', cycles_completed=len(outcomes),
                       status_counts=dict(Counter(x['status'] for x in outcomes)),
                       llm_calls=sum(x['llm_calls'] for x in outcomes), writes_attempted=0,
                       last_outcome=outcomes[-1])
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if outcomes[-1]['status'] in ('proposed', 'classified_not_question', 'noop') else 1
    except Exception:
        # No arbitrary exception, file path, response, credential or traceback output.
        print(json.dumps({'version': '0.5.2', 'status': 'local_setup_failed', 'writes_attempted': 0}))
        return 2


if __name__ == '__main__':
    sys.exit(execute())
