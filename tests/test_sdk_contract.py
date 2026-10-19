"""Real installed SDK with synthetic transport; NEVER contacts game services."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ctn.official_readonly import OfficialReadOnlySocialClient, OfficialSchemaMismatch
from ctn.question_classifier import OfficialQuestionProvider, QuestionClassifier
from ctn.reliable_readonly import run_cycle
from ctn.reliability_state import ReliabilityStore


@unittest.skipUnless(importlib.util.find_spec('capturethenarrative'), 'Install requirements-official.txt for SDK contract tests')
class SDKContractTests(unittest.TestCase):
    def setUp(self):
        import capturethenarrative as sdk
        self.sdk=sdk
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.store=ReliabilityStore(self.root/'db');self.addCleanup(self.store.close)

    def social(self, pages):
        self.calls=[]
        def response(request, **kwargs):
            self.assertEqual(request.method,'GET')
            self.calls.append(request.full_url)
            value=pages.pop(0)
            raw=io.BytesIO(json.dumps(value).encode())
            raw.headers={'X-RateLimit-Limit':'10','X-RateLimit-Remaining':'9','X-RateLimit-Reset':'60'}
            return raw
        patcher=patch('urllib.request.urlopen',side_effect=response)
        patcher.start();self.addCleanup(patcher.stop)
        return self.sdk.LegitSocial(timeout=10,max_retries=0)

    def model(self, code=200, body=None, headers=None):
        # Use OpenAI's installed HTTP library (httpx in older releases, httpx2 in v3).
        import openai
        httpx=__import__(openai.DefaultHttpxClient.__mro__[1].__module__.split('.')[0])
        self.model_requests=[]
        def transport(request):
            self.model_requests.append(request)
            return httpx.Response(code,json=body or {'id':'synthetic','choices':[{'index':0,'finish_reason':'stop',
                'message':{'role':'assistant','content':'{"is_question": true}'}}]},headers=headers or {})
        client=openai.DefaultHttpxClient(transport=httpx.MockTransport(transport))
        llm=self.sdk.CompetitorLLM(team_code='SYNTHETIC_TEST_ONLY',max_retries=0,timeout=20,http_client=client)
        self.addCleanup(llm.close)
        return QuestionClassifier(OfficialQuestionProvider('SYNTHETIC_TEST_ONLY',llm=llm))

    def test_real_sdk_feed_to_llm_synthetic_transport(self):
        social=self.social([{'items':[{'post_id':1,'author_id':2,'body':'Where?'},
            {'post_id':3,'author_id':4,'body':'Where is the library located?'}],'next_cursor':None}])
        classifier=self.model()
        out=run_cycle(OfficialReadOnlySocialClient(social=social),self.store,self.root/'log',
            official=True,classifier=classifier,stop_path=self.root/'STOP')
        self.assertEqual(out['status'],'proposed')
        self.assertEqual(out['selected_post']['post_id'],'3')
        self.assertEqual((out['official_feed_calls'],out['llm_calls']),(1,1))
        self.assertEqual(len(self.model_requests),1)
        request=json.loads(self.model_requests[0].content)
        self.assertEqual(request['max_tokens'],60)
        self.assertIn('Where is the library located?',request['messages'][0]['content'])
        self.assertEqual(out['writes_attempted'],0)

    def test_actual_openai_429_no_internal_retry(self):
        social=self.social([{'items':[{'post_id':1,'author_id':2,'body':'Where?'}]}])
        classifier=self.model(429,{'error':{'code':'rate_limit_exceeded','message':'SECRET'}},{'retry-after':'900'})
        out=run_cycle(OfficialReadOnlySocialClient(social=social),self.store,self.root/'log',
            official=True,classifier=classifier,stop_path=self.root/'STOP')
        self.assertEqual(out['status'],'rate_limited')
        self.assertEqual(out['retry_after_seconds'],900)
        self.assertEqual(len(self.model_requests),1)
        self.assertNotIn('SECRET',(self.root/'log').read_text())

    def test_actual_openai_quota_exception(self):
        classifier=self.model(429,{'error':{'code':'insufficient_quota','message':'SECRET'}})
        from ctn.replay_client import ReplaySocialClient
        from ctn.models import MockPost
        out=run_cycle(ReplaySocialClient([MockPost('1','x','Where?')]),self.store,self.root/'log',
            classifier=classifier,stop_path=self.root/'STOP')
        self.assertEqual(out['status'],'quota_exhausted')
        self.assertEqual(len(self.model_requests),1)

    def test_real_sdk_pagination_and_normalization(self):
        items=[{'post_id':i,'author_id':1,'body':f'Question {i}?'} for i in range(12)]
        social=self.social([{'items':items[:10],'next_cursor':'next'}, {'items':items[10:]}])
        client=OfficialReadOnlySocialClient(social=social,limit=20)
        posts=client.get_feed()
        self.assertEqual(len(posts),12)
        self.assertEqual(client.read_calls,2)
        self.assertIn('cursor=next',self.calls[1])

    def test_real_sdk_bad_page_fails_before_model(self):
        social=self.social([{'items':[{'post_id':1,'author_id':2,'body':[]}]}])
        classifier=self.model()
        out=run_cycle(OfficialReadOnlySocialClient(social=social),self.store,self.root/'log',
            official=True,classifier=classifier,stop_path=self.root/'STOP')
        self.assertEqual(out['status'],'feed_or_validation_failed')
        self.assertEqual(len(self.model_requests),0)

    def test_provider_constructor_has_bounded_retries_and_timeout(self):
        with patch.object(self.sdk,'CompetitorLLM') as factory:
            OfficialQuestionProvider('SYNTHETIC_TEST_ONLY')
        factory.assert_called_once_with(team_code='SYNTHETIC_TEST_ONLY',max_retries=0,timeout=20.0)
