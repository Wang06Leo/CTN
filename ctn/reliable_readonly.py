"""Bounded v052 read-only orchestration. No execute or social write path."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import re

from .decision import OfflineStructuredAgent
from .models import MockPost
from .question_classifier import ClassificationError
from .local_secrets import MissingTeamCode, LocalSecretsError
from .review_state import ReadCooldown
from .reliability_state import WorkerBusy, ServiceBlocked, CircuitOpen
from .rule_policy import OfflineRulePolicy
from .safety import ActionValidator


class InputRejected(ValueError):
    pass


def seconds(value, now):
    try:
        result = float(value)
    except (ValueError, TypeError):
        try:
            result = parsedate_to_datetime(value).timestamp() - now
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0., result) if math.isfinite(result) else None


def error_info(exc, now, stage):
    """Read only known metadata, never emit server bodies or exception strings."""
    status = getattr(exc, 'status_code', getattr(exc, 'status', None))
    status = status if type(status) is int else None
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', {})
    waits = [seconds(getattr(exc, 'retry_after', None), now),
             seconds(headers.get('retry-after'), now)]
    wait = max((x for x in waits if x is not None), default=0.)
    code = getattr(exc, 'code', None)
    body = getattr(exc, 'body', None)
    if isinstance(body, dict):
        error = body.get('error', body)
        if isinstance(error, dict):
            code = error.get('code', code)
    if code in ('insufficient_quota', 'quota_exhausted'):
        reason = 'quota_exhausted'
    elif status == 429:
        reason = 'rate_limited'
    elif status in (401, 403):
        reason = 'access_denied'
    elif isinstance(exc, ClassificationError):
        reason = 'invalid_json'
    elif isinstance(exc, InputRejected):
        reason = 'input_rejected'
    elif isinstance(exc, (MissingTeamCode, LocalSecretsError)):
        reason = 'configuration_failed'
    elif isinstance(exc, TimeoutError) or type(exc).__name__ in ('APITimeoutError', 'ReadTimeout'):
        reason = 'timeout'
    else:
        reason = 'model_failed' if stage == 'model' else 'feed_or_validation_failed'
    # SDK Feed RateLimited.retry_after truncates raw headers at 120 seconds.
    # With no original header exposed, block instead of guessing an earlier retry.
    blocked = reason in ('quota_exhausted', 'access_denied') or (stage == 'feed' and status == 429)
    return reason, status, max(wait, 60.), blocked


def candidate_queue(posts):
    if not isinstance(posts, list):
        raise ValueError('Expected normalized list')
    unique = {}
    for p in posts:
        if not isinstance(p, MockPost) or not isinstance(p.id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', p.id):
            raise ValueError('Invalid normalized post ID')
        if not isinstance(p.text, str) or not isinstance(p.author, str) or not p.author:
            raise ValueError('Invalid normalized post')
        if p.id in unique and p != unique[p.id]:
            raise ValueError('Conflicting duplicate post')
        unique.setdefault(p.id, p)
    # Stable sort preserves original Feed order on equal character counts.
    return sorted((p for p in unique.values() if '?' in p.text),
                  key=lambda p: -len(p.text))


def run_cycle(client, store, log_path, *, official=False, classifier=None,
              stop_path=Path('STOP'), policy=None):
    out = dict(version='0.5.2', status='not_started', writes_attempted=0,
               llm_calls=0, classification_calls=0, official_feed_calls=0,
               classification=None, proposed=False, reviewed=False,
               real_e2e_classification_success=False,
               timestamp_utc=datetime.now(timezone.utc).isoformat(),
               decision={'proposed_kind': 'noop', 'reason_code': 'not_started'})
    key = None
    stage = 'feed'
    feed_attempted = False
    namespace = 'official' if official else 'fixture'
    source = namespace

    def stopped():
        if stop_path.exists():
            out['status'] = 'stopped_by_kill_switch'
            return True
        return False

    def observe_limit(limit):
        remaining = getattr(limit, 'remaining', None)
        reset = seconds(getattr(limit, 'reset', None), store.clock())
        if type(remaining) in (int, float) and remaining <= 0 and reset is not None:
            store.defer('feed', reset)

    def work():
        nonlocal key, stage, feed_attempted
        if stopped():
            return
        store.guard('feed' if official else 'fixture_feed')
        if official:
            store.reserve_feed_read(65.)
        feed_attempted = True
        posts = client.get_feed()
        if official:
            observe_limit(getattr(client, 'rate_limit', None))
        ranked = candidate_queue(posts)
        out.update(received_posts=len(posts), candidate_queue=[
            {'post_id': p.id, 'length_chars': len(p.text)} for p in ranked])
        store.success('feed' if official else 'fixture_feed')
        if stopped():
            return
        matched, key = store.claim(ranked, namespace)
        if matched is None:
            out['status'] = 'noop'
            out['decision']['reason_code'] = 'no_ascii_question' if not ranked else 'no_ready_candidate'
            return
        out['selected_post'] = {'post_id': matched.id, 'length_chars': len(matched.text)}
        decision = OfflineStructuredAgent().decide([matched])
        ActionValidator().validate(decision.action, posts)
        (policy or OfflineRulePolicy()).validate(decision.action, posts)
        result = None
        if classifier is not None:
            stage = 'model'
            if stopped():
                store.release(key, 'kill_switch')
                return
            service = 'model' if classifier.is_official else 'fixture_model'
            store.guard(service)
            if classifier.is_official:
                if not 1 <= len(matched.text) <= 500:
                    raise InputRejected('Outside local official input cap')
                prepare = getattr(classifier, 'prepare', None)
                if prepare is not None:
                    prepare()
                store.reserve_model_call(25.)
                out['llm_calls'] = 1
            out['classification_calls'] = 1
            result = classifier.classify(matched.text)
            if type(result) is not bool:
                raise ClassificationError('Expected boolean classification')
            out['classification'] = result
            out['classification_source'] = 'official_llm' if classifier.is_official else 'fixture'
            store.success(service)
        if stopped():
            store.fail_candidate(key, 'kill_switch', delay=0.)
            return
        proposed = result is not False
        # Validate final structured decision before durable completion/audit.
        if proposed:
            ActionValidator().validate(decision.action, posts)
        store.complete(key, classification=result, proposed=proposed,
                       length=len(matched.text), source=source)
        out.update(status='proposed' if proposed else 'classified_not_question',
                   proposed=proposed, reviewed=True,
                   decision={'proposed_kind': 'reply' if proposed else 'noop',
                             'reason_code': 'longest_ascii_question' if proposed else 'classified_not_information_question'})
        out['real_e2e_classification_success'] = bool(official and classifier is not None
            and classifier.is_official and type(result) is bool)

    try:
        with store.worker_lock():
            # Includes failure handling under the same lock as completion.
            try:
                work()
            except (ReadCooldown, ServiceBlocked) as exc:
                out['status'] = ('service_blocked' if isinstance(exc, ServiceBlocked)
                                 else 'circuit_open' if isinstance(exc, CircuitOpen) else 'cooldown')
                if isinstance(exc, ReadCooldown):
                    out['retry_after_seconds'] = exc.seconds
                if key:
                    store.release(key, out['status'], delay=out.get('retry_after_seconds', 60.))
            except Exception as exc:
                reason, status, wait, blocked = error_info(exc, store.clock(), stage)
                out.update(status=reason, http_status=status, retry_after_seconds=wait)
                service = ('model' if classifier.is_official else 'fixture_model') if stage == 'model' else ('feed' if official else 'fixture_feed')
                store.defer(service, wait, failure=True, blocked=blocked)
                if key:
                    store.fail_candidate(key, reason, delay=wait, terminal=reason in ('invalid_json', 'input_rejected', 'access_denied', 'quota_exhausted'))
            finally:
                count = getattr(client, 'read_calls', 0) if official and feed_attempted else 0
                out['official_feed_calls'] = count if type(count) is int and count >= 0 else 0
                if out['status'] not in ('proposed', 'classified_not_question', 'noop'):
                    out['decision'] = {'proposed_kind': 'noop', 'reason_code': out['status']}
                store.record_event(out)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open('a', encoding='utf-8') as file:
                    file.write(json.dumps(out, ensure_ascii=True) + '\n')
    except WorkerBusy:
        out['status'] = 'worker_busy'
        out['decision']['reason_code'] = 'worker_busy'
        # Authoritative DB audit; do not race another process's JSONL append.
        store.record_event(out)
    return out
