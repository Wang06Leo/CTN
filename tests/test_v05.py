"""No real SDK, network or credentials are needed for any test here."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ctn.models import MockPost
from ctn.review_state import ReviewStore
from ctn.integrated_readonly import run_readonly_cycle, run_readonly_cycles
from ctn.question_classifier import (
    ClassificationError, FixtureQuestionProvider, OfficialQuestionProvider,
    QuestionClassifier, parse_classification
)


class Feed:
    def __init__(self, posts):
        self.posts = posts
    def get_feed(self):
        return list(self.posts)
    def execute(self, *_):
        raise AssertionError('never execute a social action')


class TestsV05(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'db.sqlite3'
        self.log = self.root / 'log.jsonl'
        self.stop = self.root / 'STOP'
    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_good(self):
        self.assertTrue(parse_classification('{"is_question": true}'))
        self.assertFalse(parse_classification('{"is_question": false}'))

    def test_bad_json(self):
        with self.assertRaises(ClassificationError):
            parse_classification('```json {"is_question": true} ```')

    def test_extra_field(self):
        with self.assertRaises(ClassificationError):
            parse_classification('{"is_question": true, "reply": "hack"}')

    def test_bad_type(self):
        with self.assertRaises(ClassificationError):
            parse_classification('{"is_question": 1}')

    def test_question_classification_offline_only(self):
        f = Feed([MockPost('7','1','Where is the library?')])
        c = QuestionClassifier(FixtureQuestionProvider())
        with ReviewStore(self.db) as db:
            out = run_readonly_cycle(f, db, self.log, source='mock', classifier=c, stop_path=self.stop)
        self.assertTrue(out['classification_is_question'])
        self.assertEqual(out['llm_calls'], 0)
        self.assertEqual(out['writes_attempted'], 0)

    def test_all_reviewed_has_truthful_reason(self):
        f = Feed([MockPost('7','1','Where is the library?')])
        with ReviewStore(self.db) as db:
            run_readonly_cycle(f, db, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
            out = run_readonly_cycle(f, db, self.log, source='mock', stop_path=self.stop)
        self.assertEqual(out['decision']['reason_code'], 'all_ascii_candidates_reviewed')
        self.assertEqual(out['ascii_question_candidates'], 1)

    def test_no_question_reason(self):
        with ReviewStore(self.db) as db:
            out = run_readonly_cycle(Feed([MockPost('1','x','Hello')]), db, self.log, source='mock', stop_path=self.stop)
        self.assertEqual(out['decision']['reason_code'], 'no_ascii_question')

    def test_source_namespacing(self):
        f = Feed([MockPost('7','1','Where is the library?')])
        with ReviewStore(self.db) as db:
            run_readonly_cycle(f, db, self.log, source='local_fixture', record_reviewed=True, stop_path=self.stop)
            # Test official-like input (without any network), sharing identical post ID.
            out = run_readonly_cycle(f, db, self.log, source='trending', reserve_official_read=True, stop_path=self.stop)
            self.assertEqual(db.count_reviewed(), 1)
        self.assertEqual(out['status'], 'validated_proposal_only')
        self.assertEqual(out['unreviewed_candidates'], 1)

    def test_official_classifier_simulated_sdk(self):
        llm = Mock()
        llm.ask.return_value = '{"is_question": true}'
        p = OfficialQuestionProvider('EXAMPLE_TEAM_CODE_PLACEHOLDER', llm=llm)
        got = QuestionClassifier(p).classify('Where is the library?')
        self.assertTrue(got)
        self.assertEqual(llm.ask.call_count, 1)
        self.assertEqual(llm.ask.call_args.kwargs['max_tokens'], 60)
        self.assertIn('Where is the library?', llm.ask.call_args.args[0])

    def test_model_failure_does_not_review(self):
        f = Feed([MockPost('7','1','Where?')])
        c = QuestionClassifier(FixtureQuestionProvider('{invalid'))
        with ReviewStore(self.db) as db:
            result = run_readonly_cycle(f, db, self.log, source='mock', classifier=c,
                record_reviewed=True, stop_path=self.stop)
            self.assertEqual(db.count_reviewed(), 0)
        self.assertEqual(result['status'], 'read_or_validation_failed')
        self.assertEqual(result['error_type'], 'ClassificationError')

    def test_model_failure_does_not_leak_text(self):
        f = Feed([MockPost('7','1','My private text is secret123?')])
        c = QuestionClassifier(FixtureQuestionProvider('PRIVATE_RESPONSE'))
        with ReviewStore(self.db) as db:
            run_readonly_cycle(f, db, self.log, source='mock', classifier=c, stop_path=self.stop)
        text = self.log.read_text(encoding='utf-8')
        self.assertNotIn('secret123', text)
        self.assertNotIn('PRIVATE_RESPONSE', text)

    def test_summary_model_call_count_zero_fixture(self):
        f = Feed([MockPost('7','1','Where?')])
        c = QuestionClassifier(FixtureQuestionProvider())
        with ReviewStore(self.db) as db:
            summary = run_readonly_cycles(f, db, self.log, source='mock',
                cycles=2, interval_seconds=0, classifier=c, stop_path=self.stop, sleeper=lambda _: None)
        self.assertEqual(summary['llm_calls'], 0)
        self.assertEqual(summary['cycles_completed'], 2)

    def test_official_model_call_accounted(self):
        llm = Mock()
        llm.ask.return_value = '{"is_question": true}'
        c = QuestionClassifier(OfficialQuestionProvider('EXAMPLE_TEAM_CODE_PLACEHOLDER', llm=llm))
        f = Feed([MockPost('7','1','Where?')])
        with ReviewStore(self.db) as db:
            result = run_readonly_cycle(f, db, self.log, source='trending', classifier=c,
                reserve_official_read=True, stop_path=self.stop)
        self.assertEqual(result['llm_calls'], 1)
        self.assertEqual(llm.ask.call_count, 1)

    def test_no_candidate_skips_official_model(self):
        llm = Mock()
        c = QuestionClassifier(OfficialQuestionProvider('EXAMPLE_TEAM_CODE_PLACEHOLDER', llm=llm))
        with ReviewStore(self.db) as db:
            result = run_readonly_cycle(Feed([MockPost('1','x','Hello')]), db, self.log,
                source='trending', classifier=c, reserve_official_read=True, stop_path=self.stop)
        self.assertEqual(result['llm_calls'], 0)
        llm.ask.assert_not_called()


if __name__ == '__main__':
    unittest.main()

class RateGuardTests(unittest.TestCase):
    def test_model_cooldown_persists_across_restart(self):
        from ctn.review_state import ModelCooldown
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'calls.sqlite3'
            with ReviewStore(db, clock=lambda: 100.) as state:
                state.reserve_model_call(25.)
            with ReviewStore(db, clock=lambda: 110.) as state:
                with self.assertRaises(ModelCooldown):
                    state.reserve_model_call(25.)
            with ReviewStore(db, clock=lambda: 125.1) as state:
                state.reserve_model_call(25.)

    def test_model_cooldown_does_not_count_as_model_call(self):
        llm = Mock()
        llm.ask.return_value = '{"is_question": true}'
        provider = OfficialQuestionProvider('EXAMPLE_TEAM_CODE_PLACEHOLDER', llm=llm)
        cls = QuestionClassifier(provider)
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / 'state.sqlite3'
            log = Path(tmp) / 'log.jsonl'
            with ReviewStore(dbpath, clock=lambda: 100.) as db:
                feed = Feed([MockPost('7', '1', 'Where?')])
                first = run_readonly_cycle(feed, db, log, source='trending',
                    reserve_official_read=True, classifier=cls, stop_path=Path(tmp) / 'STOP')
                second = run_readonly_cycle(feed, db, log, source='trending',
                    reserve_official_read=False, classifier=cls, stop_path=Path(tmp) / 'STOP')
            self.assertEqual(first['llm_calls'], 1)
            self.assertEqual(second['status'], 'local_model_cooldown')
            self.assertEqual(second['llm_calls'], 0)
            llm.ask.assert_called_once()
