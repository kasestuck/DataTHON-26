"""Synthetic contract tests only; experiment quality requires actual models."""
import json

import pytest

from backend.analyzer.errors import InferenceError
from backend.analyzer.vlm import generation_schema
from backend.experiments.binary_sequence import SequenceGate, binary_prompt, parse_binary, select_pilot


def test_binary_prompt_has_no_dataset_answers_and_has_routine_counterexamples():
    prompt = binary_prompt([0., 2., 5., 8.])
    assert 'BEFORE' in prompt and 'AFTER' in prompt and 'NORMAL counterexamples' in prompt
    assert 'cash_split_transfer' not in prompt and '.mov' not in prompt
    assert 'identity' in prompt and 'uncertain' in prompt


@pytest.mark.parametrize('decision,ids', [('supported', [3, 0]), ('routine', []), ('uncertain', [])])
def test_binary_decisions_accept_only_consistent_evidence(decision, ids):
    payload = {'observation': 'Действие не установлено.', 'decision': decision, 'evidence_indices': ids}
    assert parse_binary(json.dumps(payload), 4) == payload


@pytest.mark.parametrize('decision,ids', [
    ('supported', []), ('supported', [0]), ('supported', [True, 2]), ('supported', [0, 0]),
    ('supported', [-1, 2]), ('supported', [0, 4]), ('routine', [0]), ('uncertain', [0]),
])
def test_binary_invalid_json_is_an_error_not_a_negative(decision, ids):
    with pytest.raises(InferenceError):
        parse_binary(json.dumps({'observation': 'Текст', 'decision': decision, 'evidence_indices': ids}), 4)


class SyntheticClient:
    def __init__(self, decision):
        self.calls, self.decision = [], decision

    def set_response_schema(self, schema):
        self.schema = schema

    def set_window_context(self, **context):
        pass

    def infer(self, images, prompt, timeout_sec):
        self.calls.append((images, prompt, timeout_sec))
        if len(self.calls) == 1:
            return json.dumps({'observation': 'Возможно, видна последовательность.', 'decision': self.decision,
                'evidence_indices': [3, 0] if self.decision == 'supported' else []})
        return json.dumps(dict(observed_action='Предмет остаётся после ухода.', has_violation=True,
            violation_type='cash_left_unattended', timecode_sec=8., confidence=.8, evidence_indices=[3, 0]))


@pytest.mark.parametrize('decision,count', [('supported', 2), ('routine', 1), ('uncertain', 1)])
def test_classification_is_separate_bounded_and_preserves_exact_source_time(decision, count):
    client = SyntheticClient(decision)
    catalog = {'cash_left_unattended': {'name': 'Название', 'description': 'Visible departure and remaining cash'}}
    policy = {'cash_left_unattended': {'min_evidence': 2, 'min_context_sec': 3, 'enabled': True}}
    gate = SequenceGate(client, catalog, policy)
    times = [0., 2., 5., 8.]
    gate.set_response_schema(generation_schema(catalog, times))
    gate.set_window_context(window=1, attempt=0, analysis_deadline=999999999.)
    result = json.loads(gate.infer([b'jpeg'] * 4, 'UNTRUSTED original prompt and filename', 30.))
    assert len(client.calls) == count
    assert all('UNTRUSTED' not in call[1] for call in client.calls)
    assert all(call[2] <= 30 for call in client.calls)
    if decision == 'supported':
        assert result['timecode_sec'] == times[3] and result['confidence'] == .8
        assert result['evidence_indices'] == [3, 0]
        assert 'Возможно, видна последовательность.' not in client.calls[1][1]
    else:
        assert result['has_violation'] is False and result['timecode_sec'] is None


def test_pilot_is_fixed_across_dev_control_and_never_selects_jury_data():
    labels = [dict(video=v, has_violation=truth) for v, truth in [('a', True), ('b', True), ('c', False), ('d', False)]]
    baseline = dict(predictions={'a': [{}], 'b': [], 'c': [{}], 'd': []},
        run_inputs={'split': {'diagnostic': ['a', 'b', 'c', 'd'], 'control': []}})
    assert select_pilot(labels, baseline) == {'diagnostic': ['a', 'b', 'c', 'd'], 'control': []}


def test_policy_coverage_probe_preserves_original_and_all_evidence_minima():
    from copy import deepcopy
    from backend.experiments.policy_probe import experimental_policy
    policy = {'unknown': dict(enabled=False, min_evidence=3, min_context_sec=10, review_status='needs_review')}
    original = deepcopy(policy)
    changed = experimental_policy(policy)
    assert policy == original
    assert changed['unknown'] == {**original['unknown'], 'enabled': True}
