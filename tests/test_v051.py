"""Offline regression coverage for v0.5.1. No SDK calls, real credentials, or internet."""
from __future__ import annotations
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ctn.models import MockPost
from ctn.review_state import ReviewStore
from ctn.integrated_readonly import run_readonly_cycle
from ctn.question_classifier import FixtureQuestionProvider, QuestionClassifier
from ctn.local_secrets import load_team_code, MissingTeamCode, LocalSecretsError
import run_v051


class FakeClient:
    def __init__(self, posts):
        self.posts = posts
        self.read_calls = 0
    def get_feed(self):
        self.read_calls += 1
        return list(self.posts)
    def execute(self, *_):
        raise AssertionError('read-only invariant: must never execute')


class TestV051(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / 'events.jsonl'
        self.db = self.root / 'review.sqlite3'
        self.stop = self.root / 'STOP'

    def test_secret_missing(self):
        with self.assertRaises(MissingTeamCode):
            load_team_code(self.root / '.env', environ={})

    def test_secret_from_env_wins(self):
        (self.root / '.env').write_text('CTN_TEAM_CODE=other_secret\n')
        code, src = load_team_code(self.root / '.env', environ={'CTN_TEAM_CODE': 'ENV_SECRET'})
        self.assertEqual((code, src), ('ENV_SECRET','process_environment'))

    def test_secret_from_dotenv_and_no_echo(self):
        (self.root / '.env').write_text('# comment\nCTN_TEAM_CODE="SENSITIVE_TEST_VALUE"\n', encoding='utf-8')
        code, src = load_team_code(self.root / '.env', environ={})
        self.assertEqual((code, src), ('SENSITIVE_TEST_VALUE', 'local_env_file'))

    def test_duplicate_secret_is_rejected(self):
        (self.root / '.env').write_text('CTN_TEAM_CODE=ONE\nCTN_TEAM_CODE=TWO')
        with self.assertRaises(LocalSecretsError):
            load_team_code(self.root / '.env', environ={})

    def test_placeholder_not_accepted(self):
        (self.root / '.env').write_text('CTN_TEAM_CODE=REPLACE_WITH_YOUR_OWN_TEAM_CODE')
        with self.assertRaises(MissingTeamCode):
            load_team_code(self.root / '.env', environ={})

    def test_gitignore_contains_dotenv(self):
        gitignore = (run_v051.PROJECT_DIR / '.gitignore').read_text()
        self.assertIn('\n.env\n', '\n'+gitignore)

    def test_classifier_false_vetoes_reply(self):
        c = QuestionClassifier(FixtureQuestionProvider('{"is_question": false}'))
        feed = FakeClient([MockPost('1', 'author', 'What do you mean?')])
        with ReviewStore(self.db) as store:
            outcome = run_readonly_cycle(feed, store, self.log, source='mock',
                                         classifier=c, stop_path=self.stop)
        self.assertEqual(outcome['decision']['proposed_kind'], 'noop')
        self.assertEqual(outcome['status'], 'classified_not_question')
        self.assertEqual(outcome['writes_attempted'], 0)

    def test_classifier_true_keeps_local_proposal(self):
        c = QuestionClassifier(FixtureQuestionProvider('{"is_question": true}'))
        feed = FakeClient([MockPost('1', 'author', 'Where?')])
        with ReviewStore(self.db) as store:
            outcome = run_readonly_cycle(feed, store, self.log, source='mock',
                                         classifier=c, stop_path=self.stop)
        self.assertEqual(outcome['decision']['proposed_kind'], 'reply')
        self.assertEqual(outcome['status'], 'validated_proposal_only')

    def test_no_ascii_question_never_calls_model(self):
        classifier = Mock()
        classifier.is_official = False
        client = FakeClient([MockPost('1','x','Hello！')])
        with ReviewStore(self.db) as store:
            out = run_readonly_cycle(client, store, self.log, source='mock',
                                     classifier=classifier, stop_path=self.stop)
        self.assertEqual(out['decision']['reason_code'], 'no_ascii_question')
        classifier.classify.assert_not_called()

    def test_longest_selected_and_only_one_model_call(self):
        classifier = Mock()
        classifier.is_official = False
        classifier.classify.return_value = True
        client = FakeClient([MockPost('short','x','Why?'),
                             MockPost('long','x','Can you explain what happens after this?')])
        with ReviewStore(self.db) as store:
            out = run_readonly_cycle(client, store, self.log, source='mock',
                                     classifier=classifier, stop_path=self.stop)
        self.assertEqual(out['selected_post']['post_id'], 'long')
        self.assertEqual(classifier.classify.call_count, 1)

    def test_cli_requires_explicit_read_permission(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            run_v051.execute(['--source','official'])
        self.assertIn('confirm-public-read', stderr.getvalue())

    def test_cli_requires_explicit_model_permission(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            run_v051.execute(['--model','official'])
        self.assertIn('confirm-official-llm', stderr.getvalue())

    def test_check_config_does_not_initialize_network(self):
        out = io.StringIO()
        with patch('run_v051.load_team_code', return_value=('SECRET', 'local_env_file')) as mock_code, \
             patch('run_v051.OfficialReadOnlySocialClient', side_effect=AssertionError('network client initialized')):
            with redirect_stdout(out):
                status = run_v051.execute(['--check-config'])
        self.assertEqual(status, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['network_calls'], 0)
        self.assertNotIn('SECRET', out.getvalue())
        mock_code.assert_called_once()

    def test_mock_cli_offline_runs_and_no_secret_needed(self):
        out = io.StringIO()
        with redirect_stdout(out):
            status = run_v051.execute(['--source', 'mock', '--model', 'fixture',
                 '--db', str(self.db), '--log', str(self.log), '--stop-path', str(self.stop)])
        self.assertEqual(status, 0)
        summary = json.loads(out.getvalue())
        self.assertEqual(summary['last_outcome']['status'], 'validated_proposal_only')
        self.assertEqual(summary['llm_calls'], 0)
        self.assertEqual(summary['writes_attempted'], 0)

    def test_mock_official_llm_mocked_one_call(self):
        """Actual SDK call must be explicitly opted in; simulated here to avoid network."""
        mocked = Mock()
        mocked.ask.return_value = '{"is_question": true}'
        original = run_v051.OfficialQuestionProvider
        def make_mocked_provider(*args):
            return original(*args, llm=mocked)
        out = io.StringIO()
        with patch('run_v051.load_team_code', return_value=('TEST_CODE', 'local_env_file')), \
             patch('run_v051.OfficialQuestionProvider', side_effect=make_mocked_provider), \
             redirect_stdout(out):
            result = run_v051.execute(['--source', 'mock', '--model', 'official',
                 '--confirm-official-llm', '--db', str(self.db), '--log', str(self.log),
                 '--stop-path', str(self.stop)])
        self.assertEqual(result, 0)
        summary = json.loads(out.getvalue())
        self.assertEqual(summary['llm_calls'], 1)
        self.assertEqual(summary['writes_attempted'], 0)
        self.assertEqual(mocked.ask.call_count, 1)


if __name__ == '__main__':
    unittest.main()

class TestV051ReadOnlyEndToEnd(unittest.TestCase):
    def test_official_feed_and_llm_both_mocked_and_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = FakeClient([MockPost('short', 'author', 'Where?'),
                               MockPost('long', 'author', 'Can you clarify the meeting location?')])
            mock_llm = Mock()
            mock_llm.ask.return_value = '{"is_question": false}'
            from ctn.question_classifier import OfficialQuestionProvider, QuestionClassifier
            classifier = QuestionClassifier(OfficialQuestionProvider('TEST_CODE', llm=mock_llm))
            with ReviewStore(root / 'state.sqlite3') as db:
                summary = run_readonly_cycle(fake, db, root / 'log.jsonl',
                    source='trending', reserve_official_read=True,
                    classifier=classifier, stop_path=root / 'STOP')
            self.assertEqual(summary['selected_post']['post_id'], 'long')
            self.assertEqual(summary['decision']['proposed_kind'], 'noop')
            self.assertEqual(summary['classification_source'], 'official_llm')
            self.assertEqual(summary['llm_calls'], 1)
            self.assertEqual(summary['writes_attempted'], 0)
            self.assertEqual(fake.read_calls, 1)
            mock_llm.ask.assert_called_once()
