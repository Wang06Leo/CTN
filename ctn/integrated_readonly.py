"""Official-feed observation -> local decision proposal -> audit only.

There is deliberately NO execute()/reply()/post()/like()/report() call here.
OfflineStructuredAgent proposes a neutral, hard-coded test reply. No LLM calls.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable

from .decision import OfflineStructuredAgent
from .question_classifier import QuestionClassifier
from .models import MockPost
from .review_state import ReadCooldown, ModelCooldown, ReviewStore
from .rule_policy import OfflineRulePolicy
from .safety import ActionValidator


def safe_rate_limit(value: Any) -> dict | None:
    if value is None:
        return None
    result = {}
    for key in ('limit', 'remaining', 'reset'):
        x = getattr(value, key, None)
        if type(x) in (float, int) and x >= 0:
            result[key] = x
    return result or None


def run_readonly_cycle(
    client: Any, store: ReviewStore, log_path: Path, *,
    source: str, record_reviewed: bool = False,
    reserve_official_read: bool = False,
    stop_path: Path = Path('STOP'),
    minimum_gap_seconds: float = 65.,
    policy: OfflineRulePolicy | None = None,
    classifier: QuestionClassifier | None = None,
) -> dict:
    """One bounded observation; NEVER executes a platform action."""
    output = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'official_public_readonly' if reserve_official_read else 'offline_fixture_readonly',
        'source': source, 'writes_attempted': 0, 'llm_calls': 0,
        'reviewed_this_cycle': False, 'official_score': None,
        'status': 'not_started',
    }
    try:
        if stop_path.exists():
            output['status'] = 'stopped_by_kill_switch'
        else:
            if reserve_official_read:
                store.reserve_feed_read(minimum_gap_seconds)
            posts: list[MockPost] = client.get_feed()
            output['received_posts'] = len(posts)
            eligible = [p for p in posts if '?' in p.text]
            # Synthetic and official post IDs must never share review markers.
            namespace = 'official' if reserve_official_read else 'fixture'
            unreviewed = [p for p in eligible if not store.is_reviewed(f'{namespace}:{p.id}')]
            output['ascii_question_candidates'] = len(eligible)
            output['unreviewed_candidates'] = len(unreviewed)
            output['previously_reviewed_candidates'] = len(eligible) - len(unreviewed)

            # Reuse v0.3.1 Agent selection and action validation. Only the
            # proposal metadata is used, never the generated fixed mock text.
            decision = OfflineStructuredAgent().decide(unreviewed)
            output['decision'] = {
                'reason_code': decision.reason_code,
                'source': decision.source,
                'proposed_kind': decision.action.kind,
            }
            if decision.action.kind == 'noop':
                output['status'] = 'no_unreviewed_candidate'
                output['decision']['reason_code'] = (
                    'all_ascii_candidates_reviewed' if eligible else 'no_ascii_question'
                )
            else:
                ActionValidator(max_chars=200).validate(decision.action, posts)
                if policy is not None:
                    policy.validate(decision.action, posts)
                matched = next(p for p in unreviewed if p.id == decision.action.post_id)
                output['selected_post'] = {
                    'post_id': matched.id, 'length_chars': len(matched.text)
                }
                output['status'] = 'validated_proposal_only'
                if classifier is not None:
                    # Classification is optional; the result remains local only.
                    # The official SDK limits post text to 500 characters. Reject
                    # overlong text before reserving a model attempt.
                    if classifier.is_official:
                        if not 1 <= len(matched.text) <= 500:
                            raise ValueError('selected post length outside official classification limit')
                        store.reserve_model_call(25.)
                        output['llm_calls'] = 1  # high-level SDK attempt; internal retries excluded
                    is_question = classifier.classify(matched.text)
                    output['classification_is_question'] = is_question
                    output['classification_source'] = (
                        'official_llm' if classifier.is_official else 'offline_fixture'
                    )
                    if not is_question:
                        # An ASCII question mark alone is not necessarily a
                        # question. The classifier can veto the local proposal.
                        output['decision'] = {
                            'reason_code': 'classified_not_information_question',
                            'source': output['classification_source'],
                            'proposed_kind': 'noop',
                        }
                        output['status'] = 'classified_not_question'
                if record_reviewed:
                    store.mark_reviewed(f'{namespace}:{matched.id}', len(matched.text), source)
                    output['reviewed_this_cycle'] = True
                    output['status'] = 'marked_reviewed_locally'
            if reserve_official_read:
                output['official_network_calls'] = getattr(client, 'read_calls', None)
                output['rate_limit'] = safe_rate_limit(getattr(client, 'rate_limit', None))
    except ModelCooldown as exc:
        output['status'] = 'local_model_cooldown'
        output['retry_after_seconds'] = round(exc.seconds, 1)
    except ReadCooldown as exc:
        output['status'] = 'local_read_cooldown'
        output['retry_after_seconds'] = round(exc.seconds, 1)
    except Exception as exc:
        output['status'] = 'read_or_validation_failed'
        output['error_type'] = type(exc).__name__
        status = getattr(exc, 'status', None)
        if type(status) is int:
            output['http_status'] = status
        retry_after = getattr(exc, 'retry_after', None)
        if type(retry_after) in (float, int):
            output['retry_after_seconds'] = retry_after
        # DO NOT log exception messages / bodies; they may include post text,
        # credentials, PII or privately scoped data.

    selected = output.get('selected_post', {})
    store.audit(source=source, status=output['status'],
                observed=output.get('received_posts', 0),
                eligible=output.get('ascii_question_candidates', 0),
                selected_id=selected.get('post_id'),
                error_type=output.get('error_type'))
    output['reviewed_in_sqlite_total'] = store.count_reviewed()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as file:
        file.write(json.dumps(output, ensure_ascii=False) + '\n')
    return output


def run_readonly_cycles(
    client: Any, store: ReviewStore, log_path: Path, *,
    source: str, cycles: int = 1, interval_seconds: float = 65.,
    record_reviewed: bool = False, official: bool = False,
    stop_path: Path = Path('STOP'),
    sleeper: Callable[[float], None] = time.sleep,
    policy: OfflineRulePolicy | None = None,
    classifier: QuestionClassifier | None = None,
) -> dict:
    if type(cycles) is not int or not 1 <= cycles <= (20 if official else 500):
        raise ValueError('cycles outside safe limit')
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float)) or not 0 <= interval_seconds <= 3600:
        raise ValueError('invalid interval_seconds')
    if official and cycles > 1 and interval_seconds < 65:
        raise ValueError('official multi-cycle poll interval must be >= 65 seconds')
    results = []
    stop_reason = 'cycle_limit'
    for index in range(cycles):
        output = run_readonly_cycle(client, store, log_path, source=source,
            record_reviewed=record_reviewed, reserve_official_read=official,
            stop_path=stop_path, policy=policy, classifier=classifier)
        results.append(output)
        if output['status'] == 'stopped_by_kill_switch':
            stop_reason = 'kill_switch'
            break
        if output['status'] in ('read_or_validation_failed', 'local_read_cooldown', 'local_model_cooldown'):
            stop_reason = output['status']
            break
        if index + 1 < cycles:
            try:
                sleeper(interval_seconds)
            except KeyboardInterrupt:
                stop_reason = 'keyboard_interrupt'
                break
    return {
        'mode': 'official_public_readonly' if official else 'offline_fixture_readonly',
        'cycles_requested': cycles,
        'cycles_completed': len(results),
        'status_counts': dict(Counter(x['status'] for x in results)),
        'received_post_count_total': sum(x.get('received_posts', 0) for x in results),
        'locally_reviewed_total': store.count_reviewed(),
        'stop_reason': stop_reason,
        'last_outcome': results[-1] if results else None,
        'writes_attempted': 0,
        'llm_calls': sum(int(x.get('llm_calls', 0)) for x in results),
        'official_score': None,
    }
