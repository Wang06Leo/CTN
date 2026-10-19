"""All tests use synthetic data; no official service request or platform write."""
import io
import json
import multiprocessing
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ctn.models import MockPost
from ctn.official_readonly import OfficialReadOnlySocialClient, OfficialSchemaMismatch
from ctn.question_classifier import QuestionClassifier, FixtureQuestionProvider, OfficialQuestionProvider, ClassificationError, parse_classification
from ctn.reliability_state import ReliabilityStore, WorkerBusy, CircuitOpen, ServiceBlocked
from ctn.reliable_readonly import run_cycle, candidate_queue, error_info, seconds
import run_v052


def child_lock(path, connection):
    with ReliabilityStore(Path(path)) as store:
        with store.worker_lock():
            connection.send('locked')
            connection.recv()


def concurrent_cycle(path, connection):
    with ReliabilityStore(Path(path)) as store:
        result = run_cycle(Feed([MockPost('a', 'x', 'Where?')]), store,
                           Path(path).with_suffix('.jsonl'), classifier=fixture(),
                           stop_path=Path(path).with_suffix('.stop'))
        connection.send(result['status'])


class Feed:
    def __init__(self, posts):
        self.posts = posts
        self.calls = 0
    def get_feed(self):
        self.calls += 1
        return self.posts
    def execute(self, *args):
        raise AssertionError('Forbidden social write')


def fixture(raw='{"is_question": true}'):
    return QuestionClassifier(FixtureQuestionProvider(raw))


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'state.sqlite3'
        self.log = self.root / 'events.jsonl'
        self.stop = self.root / 'STOP'
        self.time = [1000.]
        self.store = ReliabilityStore(self.db, clock=lambda: self.time[0])
        self.addCleanup(self.store.close)
        self.feed = Feed([MockPost('a', 'x', 'Why?'), MockPost('b', 'y', 'Where is the library?')])
        # Hard fail if an automated test accidentally tries real networking.
        self.net = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network disabled'))
        self.net.start()
        self.addCleanup(self.net.stop)

    def cycle(self, feed=None, classifier=None, **kw):
        return run_cycle(feed or self.feed, self.store, self.log,
                         classifier=classifier if classifier is not None else fixture(),
                         stop_path=self.stop, **kw)

    def test_queue_advances_and_deduplicates(self):
        self.feed.posts.append(self.feed.posts[1])
        a, b, c = self.cycle(), self.cycle(), self.cycle()
        self.assertEqual([a['selected_post']['post_id'], b['selected_post']['post_id']], ['b', 'a'])
        self.assertEqual(len(a['candidate_queue']), 2)
        self.assertEqual(c['status'], 'noop')
        self.assertEqual(c['classification_calls'], 0)
        self.assertEqual(self.store.count_reviewed(), 2)

    def test_ties_and_ascii_only(self):
        posts = [MockPost('z', 'x', '长' * 100 + '？'), MockPost('b', 'x', 'Why?'), MockPost('a', 'x', 'How?')]
        self.assertEqual([p.id for p in candidate_queue(posts)], ['b', 'a'])

    def test_conflicting_duplicate_rejected_before_model(self):
        model = Mock(is_official=False)
        result = self.cycle(Feed([MockPost('a','x','Why?'), MockPost('a','x','Longer question?')]), model)
        self.assertEqual(result['status'], 'feed_or_validation_failed')
        model.classify.assert_not_called()

    def test_invalid_normalized_data(self):
        for posts in (None, [None], [MockPost('', 'x', '?')], [MockPost('a','x',None)],
                      [MockPost('secret=bad', 'x', '?')], [MockPost('a','', '?')]):
            with self.subTest(posts=posts), self.assertRaises(ValueError):
                candidate_queue(posts)

    def test_empty_and_no_ascii_skip_official_model(self):
        for posts in ([], [MockPost('c', 'x', '中文？')]):
            llm = Mock(is_official=True)
            out = self.cycle(Feed(posts), llm)
            self.assertEqual(out['status'], 'noop')
            self.assertFalse(out['real_e2e_classification_success'])
            self.assertEqual(out['llm_calls'], 0)
            llm.classify.assert_not_called()

    def test_false_is_classified_reviewed_not_proposed(self):
        out = self.cycle(classifier=fixture('{"is_question": false}'))
        row = self.store.connection.execute('SELECT status,classification,proposed,reviewed FROM candidate_state').fetchone()
        self.assertEqual(row, ('classified',0,0,1))
        self.assertEqual(out['status'], 'classified_not_question')
        self.assertEqual(out['decision']['proposed_kind'], 'noop')
        self.assertEqual(self.cycle()['selected_post']['post_id'], 'a')

    def test_true_is_local_proposal_only(self):
        out = self.cycle()
        self.assertEqual(out['status'], 'proposed')
        self.assertTrue(out['reviewed'])
        self.assertFalse(out['real_e2e_classification_success'])
        self.assertEqual(out['writes_attempted'], 0)

    def test_no_model_review_advances(self):
        out = run_cycle(self.feed,self.store,self.log,stop_path=self.stop)
        self.assertIsNone(out['classification'])
        self.assertEqual(out['classification_calls'], 0)
        self.assertEqual(self.cycle()['selected_post']['post_id'], 'a')

    def test_malformed_is_terminal_not_reviewed_or_success(self):
        out = self.cycle(classifier=fixture('PRIVATE malformed response'))
        self.assertEqual(out['status'], 'invalid_json')
        self.assertFalse(out['reviewed'])
        self.assertEqual(out['decision']['proposed_kind'], 'noop')
        self.assertEqual(self.store.count_reviewed(), 0)
        self.assertEqual(self.store.connection.execute('SELECT status FROM candidate_state').fetchone()[0], 'failed_terminal')
        self.time[0] += 61
        self.assertEqual(self.cycle()['selected_post']['post_id'], 'a')

    def test_strict_json(self):
        for value in ('{"is_question":true,"is_question":false}', '{"is_question":1}',
                      '[]','{"is_question":true,"extra":0}', '```json\n{}\n```', 'x'*1001):
            with self.subTest(value=value), self.assertRaises(ClassificationError):
                parse_classification(value)

    def test_timeout_distinct_and_retry_bounded(self):
        llm = Mock(is_official=True)
        llm.classify.side_effect = TimeoutError('SECRET')
        single = Feed([MockPost('a','x','Why?')])
        for _ in range(3):
            self.assertEqual(self.cycle(single,llm)['status'], 'timeout')
            self.time[0] += 61
        self.assertEqual(self.cycle(single,llm)['status'], 'noop')
        self.assertEqual(llm.classify.call_count, 3)
        self.assertEqual(self.store.count_reviewed(), 0)

    def test_circuit_breaker_persists_and_blocks_new_candidates(self):
        model = Mock(is_official=True)
        model.classify.side_effect = ConnectionError('SECRET')
        for i in range(3):
            self.cycle(Feed([MockPost(str(i),'x','Why?')]), model)
            self.time[0] += 61
        with ReliabilityStore(self.db,clock=lambda:self.time[0]) as reopened:
            with self.assertRaises(CircuitOpen):
                reopened.guard('model')
        out = self.cycle(Feed([MockPost('new','x','Why?')]), model)
        self.assertEqual(out['status'], 'circuit_open')
        self.assertEqual(out['llm_calls'], 0)
        self.time[0] += 301
        model.classify.side_effect = None
        model.classify.return_value = True
        self.assertEqual(self.cycle(Feed([MockPost('new','x','Why?')]),model)['status'], 'proposed')
        self.store.guard('model')

    def test_server_retry_after_extends_across_restart(self):
        error = RuntimeError('PRIVATE response')
        error.status_code = 429
        error.response = SimpleNamespace(headers={'retry-after':'600'})
        llm = Mock(is_official=True)
        llm.classify.side_effect = error
        out = self.cycle(classifier=llm)
        self.assertEqual(out['status'], 'rate_limited')
        self.assertEqual(out['retry_after_seconds'],600.)
        self.time[0] += 100
        with ReliabilityStore(self.db,clock=lambda:self.time[0]) as reopened:
            from ctn.review_state import ReadCooldown
            with self.assertRaises(ReadCooldown): reopened.guard('model')
        out = self.cycle(classifier=llm)
        self.assertEqual(out['llm_calls'],0)
        self.assertEqual(llm.classify.call_count,1)

    def test_quota_distinct_and_blocks_without_retry(self):
        error = RuntimeError('SECRET')
        error.status_code = 429
        error.body = {'error':{'code':'insufficient_quota'}}
        llm = Mock(is_official=True)
        llm.classify.side_effect = error
        out = self.cycle(classifier=llm)
        self.assertEqual(out['status'], 'quota_exhausted')
        self.time[0] += 10000
        self.assertEqual(self.cycle(classifier=llm)['status'], 'service_blocked')
        self.assertEqual(llm.classify.call_count,1)

    def test_feed_429_clamped_sdk_fails_closed(self):
        error = RuntimeError('SECRET')
        error.status=429
        error.retry_after=120
        feed=Mock()
        feed.get_feed.side_effect=error
        out=self.cycle(feed,official=True)
        self.assertEqual(out['status'],'rate_limited')
        self.time[0]+=10000
        with self.assertRaises(ServiceBlocked): self.store.guard('feed')

    def test_feed_exhaustion_header_honored(self):
        feed=self.feed
        feed.rate_limit=SimpleNamespace(remaining=0, reset=1000)
        self.cycle(feed,official=True)
        self.time[0]+=66
        out=self.cycle(feed,official=True)
        self.assertEqual(out['status'],'cooldown')
        self.assertEqual(feed.calls,1)

    def test_network_failure_feed_distinct_and_no_model(self):
        feed=Mock()
        feed.get_feed.side_effect=ConnectionError('SECRET')
        llm=Mock(is_official=True)
        out=self.cycle(feed,llm,official=True)
        self.assertEqual(out['status'],'feed_or_validation_failed')
        llm.classify.assert_not_called()

    def test_retry_after_http_date_and_nan(self):
        self.assertEqual(seconds('Thu, 01 Jan 1970 00:20:00 GMT',1000),200)
        self.assertIsNone(seconds('NaN',1000))
        self.assertIsNone(seconds('bad',1000))
        self.assertEqual(seconds('-1',1000),0)

    def test_restart_excludes_completed_and_legacy_review(self):
        self.cycle()
        with ReliabilityStore(self.db) as reopened:
            reopened.mark_reviewed('fixture:a',4,'fixture')
            out=run_cycle(self.feed,reopened,self.log,classifier=fixture(),stop_path=self.stop)
        self.assertEqual(out['status'],'noop')

    def test_fixture_and_official_namespaces_isolated(self):
        self.cycle()
        out=self.cycle(official=True)
        self.assertEqual(out['selected_post']['post_id'],'b')
        self.assertEqual(self.store.count_reviewed(),2)

    def test_recover_processing_after_crash(self):
        # A previous process died after committing its claim; OS lock is now free.
        self.store.claim(candidate_queue(self.feed.posts),'fixture')
        with ReliabilityStore(self.db) as reopened:
            out=run_cycle(self.feed,reopened,self.log,classifier=fixture(),stop_path=self.stop)
        self.assertEqual(out['status'],'proposed')
        self.assertEqual(out['selected_post']['post_id'],'b')

    def test_kill_before_feed(self):
        self.stop.touch()
        out=self.cycle()
        self.assertEqual(out['status'],'stopped_by_kill_switch')
        self.assertEqual(self.feed.calls,0)

    def test_kill_during_feed_prevents_model(self):
        def read():
            self.stop.touch()
            return self.feed.posts
        feed=Mock();feed.get_feed.side_effect=read
        model=Mock(is_official=True)
        self.assertEqual(self.cycle(feed,model)['status'],'stopped_by_kill_switch')
        model.classify.assert_not_called()

    def test_kill_during_model_prevents_completion(self):
        model=Mock(is_official=False)
        def classify(_): self.stop.touch();return True
        model.classify.side_effect=classify
        out=self.cycle(classifier=model)
        self.assertEqual(out['status'],'stopped_by_kill_switch')
        self.assertEqual(self.store.count_reviewed(),0)

    def test_audit_no_secrets_raw_posts_or_errors(self):
        body='SENSITIVE_BODY?'
        self.cycle(Feed([MockPost('x','x',body)]),fixture('SECRET_TOKEN raw response'))
        data=self.log.read_text()+str(self.store.connection.execute('SELECT event_json FROM reliability_audit').fetchall())
        for secret in (body,'SECRET_TOKEN','raw response'):
            self.assertNotIn(secret,data)
        self.assertNotIn('published',data)

    def test_local_cooldown_does_not_exhaust_candidate_attempts(self):
        llm=Mock(is_official=True);llm.classify.return_value=True
        self.store.reserve_model_call(25)
        self.assertEqual(self.cycle(classifier=llm)['status'],'cooldown')
        self.assertEqual(self.store.connection.execute('SELECT attempts FROM candidate_state').fetchone()[0],0)
        self.time[0]+=26
        self.assertEqual(self.cycle(classifier=llm)['status'],'proposed')

    def test_only_one_official_model_attempt_per_cycle(self):
        llm=Mock(is_official=True);llm.classify.return_value=True
        out=self.cycle(classifier=llm,official=True)
        self.assertEqual(out['llm_calls'],1)
        llm.classify.assert_called_once_with('Where is the library?')

    def test_missing_credential_is_not_counted_as_llm_request(self):
        model = QuestionClassifier(run_v052.LazyOfficialProvider())
        from ctn.local_secrets import MissingTeamCode
        with patch('run_v052.load_team_code', side_effect=MissingTeamCode('SECRET')):
            out = self.cycle(classifier=model)
        self.assertEqual(out['status'], 'configuration_failed')
        self.assertEqual(out['llm_calls'], 0)

    def test_overlong_model_input_is_not_bad_json_or_attempt(self):
        model = Mock(is_official=True)
        out = self.cycle(Feed([MockPost('long','x','?'+'x'*500)]), model)
        self.assertEqual(out['status'],'input_rejected')
        self.assertEqual(out['llm_calls'],0)
        model.classify.assert_not_called()

    def test_blocked_feed_cycle_reports_zero_new_requests(self):
        self.feed.read_calls = 1
        self.cycle(official=True)
        out = self.cycle(official=True)
        self.assertEqual(out['status'],'cooldown')
        self.assertEqual(out['official_feed_calls'],0)

    def test_jsonl_failure_retains_sqlite_audit_and_completion(self):
        self.log.mkdir()
        with self.assertRaises(IsADirectoryError):
            self.cycle()
        self.assertEqual(self.store.count_reviewed(),1)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM reliability_audit').fetchone()[0],1)
        self.log.rmdir()
        self.assertEqual(self.cycle()['selected_post']['post_id'],'a')

    def test_cross_process_lock_and_release_on_exit(self):
        ctx=multiprocessing.get_context('spawn')
        parent,child=ctx.Pipe()
        process=ctx.Process(target=child_lock,args=(str(self.db),child))
        process.start()
        self.addCleanup(lambda: process.kill() if process.is_alive() else None)
        self.assertTrue(parent.poll(15))
        self.assertEqual(parent.recv(),'locked')
        out=self.cycle()
        self.assertEqual(out['status'],'worker_busy')
        self.assertEqual(self.feed.calls,0)
        process.terminate();process.join(10)
        self.assertFalse(process.is_alive())
        self.assertEqual(self.cycle()['status'],'proposed')
        parent.close();child.close()

    def test_two_processes_do_not_duplicate_review(self):
        ctx=multiprocessing.get_context('spawn')
        pairs=[ctx.Pipe() for _ in range(2)]
        processes=[ctx.Process(target=concurrent_cycle,args=(str(self.db),p[1])) for p in pairs]
        for process in processes: process.start()
        statuses=[]
        for pair,process in zip(pairs,processes):
            self.assertTrue(pair[0].poll(15));statuses.append(pair[0].recv())
            process.join(10);self.assertEqual(process.exitcode,0)
            pair[0].close();pair[1].close()
        self.assertEqual(statuses.count('proposed'),1)
        self.assertEqual(self.store.count_reviewed(),1)

    def test_offline_100_300_500_cycles(self):
        for total in (100,300,500):
            with self.subTest(cycles=total), ReliabilityStore(self.root / f'{total}.sqlite3') as store:
                feed=Feed([MockPost(str(i),'x','?'+'x'*i) for i in range(20)])
                results=[run_cycle(feed,store,self.root/f'{total}.jsonl',classifier=fixture(),stop_path=self.stop) for _ in range(total)]
                self.assertEqual([r['selected_post']['post_id'] for r in results[:20]],list(map(str,range(19,-1,-1))))
                self.assertTrue(all(r['status']=='noop' for r in results[20:]))
                self.assertEqual(sum(r['classification_calls'] for r in results),20)
                self.assertEqual(sum(r['llm_calls'] for r in results),0)
                self.assertEqual(store.count_reviewed(),20)
                self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM reliability_audit').fetchone()[0],total)
                self.assertTrue(all(r['writes_attempted']==0 for r in results))


class CliTests(unittest.TestCase):
    def test_explicit_network_flags_and_official_budget(self):
        for args in (['--source','official'], ['--model','official'],
                     ['--source','official','--confirm-public-read','--cycles','2'],
                     ['--model','official','--confirm-official-llm','--cycles','2'],
                     ['--cycles','501'],['--limit','11']):
            with self.subTest(args=args), patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit):
                run_v052.execute(args)

    def test_default_cli_offline_and_restart_queue(self):
        with tempfile.TemporaryDirectory() as td:
            args=['--db',str(Path(td)/'state.db'),'--log',str(Path(td)/'events.jsonl')]
            with patch.object(socket.socket,'connect',side_effect=AssertionError('no network')):
                results=[]
                for _ in range(2):
                    out=io.StringIO()
                    with patch('sys.stdout',new=out): self.assertEqual(run_v052.execute(args),0)
                    results.append(json.loads(out.getvalue()))
                self.assertNotEqual(results[0]['last_outcome'].get('selected_post'),results[1]['last_outcome'].get('selected_post'))
                self.assertTrue(all(r['llm_calls']==0 for r in results))

    def test_no_candidate_does_not_load_team_code(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'empty.json').write_text('{"posts":[]}')
            with patch('sys.stdout',new=io.StringIO()),patch('run_v052.load_team_code',side_effect=AssertionError('unnecessary credential read')):
                self.assertEqual(run_v052.execute(['--model','official','--confirm-official-llm',
                    '--fixture',str(root/'empty.json'),'--db',str(root/'state.db'),'--log',str(root/'log')]),0)

    def test_malformed_fixture_returns_safe_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'bad.json').write_text('SECRET')
            out=io.StringIO()
            with patch('sys.stdout',new=out):
                code=run_v052.execute(['--fixture',str(root/'bad.json')])
            self.assertEqual(code,2)
            self.assertNotIn('SECRET',out.getvalue())
