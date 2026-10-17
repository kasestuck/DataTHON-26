from dataclasses import replace
import json

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.detector import MockDetector
from backend.analyzer.pipeline import Analyzer
from backend.analyzer.quality_prompt import build_sequence_prompt, evidence_options
from backend.analyzer.vlm import MockVLM
from backend.analyzer.vlm import bind_evidence_timestamp, generation_schema
from backend.analyzer.errors import InferenceError
from backend.scripts.evaluate import evaluate
from backend.analyzer.candidates import CandidateSelector
from backend.analyzer.events import Event
from backend.analyzer.video import Frame
from backend.scripts.verify_candidates import checker_schema, checker_prompt, parse_check, context_frames
import pytest
from backend.scripts.quality_experiment import grouped_split
from backend.scripts.diagnose_quality import audit_rows
from backend.scripts.check_robustness import transform_frame, derived_video


def test_evidence_options_keep_original_temporal_policy():
    times = [0., 2., 4., 6., 8., 10.]
    options = evidence_options(times, {'min_evidence': 3, 'min_context_sec': 6.})
    assert options
    assert all(len(ids) == 3 and len(set(ids)) == 3 and times[ids[-1]] - times[ids[0]] >= 6 for ids in options)
    assert evidence_options([0., 1.], {'min_evidence': 3, 'min_context_sec': 6}) == []


def test_prompt_uses_only_catalog_and_actual_frame_metadata():
    prompt = build_sequence_prompt({'synthetic': {'name': 'Synthetic', 'description': 'Visible synthetic action'}},
        [0., .5, 1., 1.5])
    assert 'BEFORE' in prompt and 'AFTER' in prompt and 'insufficient information' in prompt
    assert 'same event' in prompt.lower() and 'synthetic' in prompt
    assert '.mov' not in prompt and 'organizer' not in prompt


def test_grouped_split_cannot_leak_identical_video_content():
    labels = [{'video': f'{i}.mov', 'has_violation': str(i < 4).lower()} for i in range(8)]
    predictions = {r['video']: [1] if i % 2 else [] for i, r in enumerate(labels)}
    hashes = {r['video']: str(i // 2) for i, r in enumerate(labels)}
    split = grouped_split(labels, predictions, hashes)
    assert {hashes[v] for v in split['diagnostic']}.isdisjoint(hashes[v] for v in split['control'])
    assert set(split['diagnostic']) | set(split['control']) == set(hashes)
    assert grouped_split(labels, predictions, hashes) == split


def test_window_audit_records_rejected_response_without_changing_finding(synthetic_video, tmp_path):
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'synthetic': {'name': 'Synthetic', 'description': 'Visible'}}))
    config = replace(AnalyzerConfig(catalog_path=str(catalog)), record_window_audit=True)
    report = Analyzer(MockDetector(), MockVLM(), config, allow_mock=True).analyze(str(synthetic_video))
    assert report.findings == [] and report.window_audit
    assert all(w['outcome'] == 'no_supported_catalog_event' for w in report.window_audit)
    assert len(report.window_audit[0]['frames'][0]['sha256']) == 64
    assert config.prompt_version == 'baseline'


def test_diagnosis_does_not_claim_unknown_baseline_causes():
    labels = [{'video': 'synthetic.mov', 'has_violation': 'true', 'timecode_sec': 4.,
               'violation_type': 'unknown', 'proposed_type': 'uncertain', 'review_status': 'needs_review'}]
    report = {'predictions': {'synthetic.mov': []}, 'run_inputs': {'policy': {'uncertain': {'enabled': False}}},
        'timings': {'synthetic.mov': {'candidate_windows': 1, 'diagnostic_reasons': {'insufficient_temporal_evidence': 1}}}}
    row = audit_rows(labels, report)[0]
    assert row['source_time_in_window_span'] is None and row['event_filtered'].startswith('unknown')
    assert row['detector_error'].startswith('unverified')
    assert any(c['category'] == 'policy_disabled' for c in row['cause_assessment'])


def test_robustness_transforms_preserve_original_and_frame_count(synthetic_video, tmp_path):
    import numpy as np
    image = np.arange(80 * 160 * 3, dtype=np.uint8).reshape(80, 160, 3)
    before = image.copy()
    for kind in ('mirror', 'scale', 'crop', 'lighting'):
        changed = transform_frame(image, kind)
        assert changed.ndim == 3 and changed.dtype == np.uint8
        assert np.array_equal(image, before)
    original = synthetic_video.read_bytes()
    metadata = derived_video(synthetic_video, tmp_path / 'mirror.mov', 'mirror')
    assert metadata['frames'] == 30 and synthetic_video.read_bytes() == original


def test_frame_bound_time_uses_exact_first_selected_evidence_without_invention():
    catalog = {'synthetic': {'name': 'Synthetic', 'description': 'Visible'}}
    raw = dict(observed_action='Synthetic action', has_violation=True, violation_type='synthetic',
               confidence=.87, evidence_indices=[2, 0])
    result = bind_evidence_timestamp(raw, catalog, [0., .333333333333, .666666666667])
    assert result.timecode_sec == .666666666667 and result.confidence == .87
    assert result.evidence_indices == (2, 0)
    for changes in ({'evidence_indices': [99]}, {'evidence_indices': [True]},
                    {'timecode_sec': .5}, {'evidence_indices': []}):
        with pytest.raises(InferenceError):
            bind_evidence_timestamp({**raw, **changes}, catalog, [0., .5, 1.])
    negative = {**raw, 'has_violation': False, 'violation_type': None, 'evidence_indices': []}
    assert bind_evidence_timestamp(negative, catalog, [0., 1.]).timecode_sec is None


def test_frame_bound_schema_has_no_free_floating_timestamp():
    schema = generation_schema({'synthetic': {'name': 'Synthetic', 'description': 'Visible'}}, [0., 1., 2.],
                               version='frame_bound_v2')
    assert 'timecode_sec' not in schema['properties'] and 'timecode_sec' not in schema['required']
    assert schema['properties']['evidence_indices']['items']['enum'] == [0, 1, 2]


def test_frame_bound_pipeline_preserves_public_finding_and_temporal_proof(synthetic_video, tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'synthetic': {'name': 'Synthetic', 'description': 'Visible'}}))
    response = dict(observed_action='Synthetic', has_violation=True, violation_type='synthetic',
                    confidence=.9, evidence_indices=[2, 0])
    config = AnalyzerConfig(catalog_path=str(path), prompt_version='frame_bound_v2', record_window_audit=True)
    result = Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))
    assert result.findings
    f = result.findings[0]
    assert f.timecode_sec == f.evidence_frames[0].timestamp_sec
    assert isinstance(f.evidence_frames[0].jpeg_bytes, bytes) and len(f.evidence_frames) == 2


def test_experiment_stops_on_error_without_negative_substitution(tmp_path):
    rows = [dict(video=name, has_violation='false', timecode_sec='', violation_type='', review_status='negative')
            for name in ('a.mov', 'b.mov')]
    calls = []

    def fail(path):
        calls.append(path)
        raise InferenceError('Synthetic failure')

    report = evaluate(rows, tmp_path, runner=fail, stop_on_error=True)
    assert len(calls) == 1 and report['predictions'] == {} and len(report['skipped']) == 1
    assert report['video_detection'] is None


def test_actual_trigger_frame_with_short_action_is_retained_at_same_image_budget():
    import numpy as np
    old = CandidateSelector(context_sec=10, sample_fps=2, coverage_sec=1000, min_gap_sec=0, strategy='uniform')
    new = CandidateSelector(context_sec=10, sample_fps=2, coverage_sec=1000, min_gap_sec=0, strategy='trigger')
    before, after = [], []
    for i in range(32):
        frame = Frame(i, i / 2, np.full((16, 16, 3), 255 if i == 20 else 0, np.uint8))
        events = [Event('scene_change', i / 2, {}, .9)] if i == 20 else []
        for selector, target in ((old, before), (new, after)):
            candidate = selector.update(frame, events)
            if candidate:
                target.append(candidate)
    assert len(before) == len(after) == 2
    assert len(before[1].frames) == len(after[1].frames) == 6
    assert not any(f.bgr.any() for f in before[1].frames)
    assert any(f.bgr.any() for f in after[1].frames)
    assert after[1].trigger_timestamp_sec == 10.
    assert before[1].frames[0].timestamp_sec == after[1].frames[0].timestamp_sec
    assert before[1].frames[-1].timestamp_sec == after[1].frames[-1].timestamp_sec


def test_event_anchor_adds_actual_model_chosen_frame_after_original_proof_passes(synthetic_video, tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'synthetic': {'name': 'Synthetic', 'description': 'Visible'}}))
    response = dict(observed_action='Synthetic', has_violation=True, violation_type='synthetic',
                    confidence=.9, timecode_sec=1., evidence_indices=[0, 1])
    config = AnalyzerConfig(catalog_path=str(path), include_event_anchor=True)
    report = Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))
    f = report.findings[0]
    assert f.timecode_sec == 1. and len(f.evidence_frames) == 3
    assert f.evidence_frames[0].timestamp_sec == f.timecode_sec
    policy = tmp_path / 'violation_type_policy.json'
    policy.write_text(json.dumps({'synthetic': {'enabled': True, 'min_evidence': 2, 'min_context_sec': 2.}}))
    result = Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))
    assert result.findings == [] and 'insufficient_temporal_evidence' in result.diagnostics


def test_anchor_never_replaces_three_original_proof_frames(synthetic_video, tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'synthetic': {'name': 'Synthetic', 'description': 'Visible'}}))
    response = dict(observed_action='Synthetic', has_violation=True, violation_type='synthetic',
                    confidence=.9, timecode_sec=1.5, evidence_indices=[0, 1, 2])
    config = AnalyzerConfig(catalog_path=str(path))
    report = Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))
    assert report.findings == [] and 'event_anchor_not_supported' in report.diagnostics


def test_anchor_rejects_an_invented_time_between_source_frames(synthetic_video, tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'synthetic': {'name': 'Synthetic', 'description': 'Visible'}}))
    response = dict(observed_action='Synthetic', has_violation=True, violation_type='synthetic',
                    confidence=.9, timecode_sec=1.1, evidence_indices=[0, 2])
    config = AnalyzerConfig(catalog_path=str(path))
    with pytest.raises(InferenceError, match='source frame'):
        Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))


def test_fact_verifier_preserves_uncertainty_and_original_temporal_requirements():
    rule = {'min_evidence': 3, 'min_context_sec': 6.}
    supported = json.dumps({'checks': ['visible'] * 3, 'evidence_indices': [1, 0, 2]})
    assert parse_check(supported, [0., 3., 6.], rule)[1] == 'supported'
    weak = json.dumps({'checks': ['visible'] * 3, 'evidence_indices': [0]})
    assert parse_check(weak, [0., 3., 6.], rule)[1] == 'insufficient_temporal_evidence'
    uncertain = json.dumps({'checks': ['visible', 'uncertain', 'visible'], 'evidence_indices': []})
    assert parse_check(uncertain, [0., 3., 6.], rule)[1] == 'insufficient_information'
    with pytest.raises(InferenceError):
        parse_check(json.dumps({'checks': ['visible'] * 3, 'evidence_indices': [99]}), [0.], rule)


def test_fact_verifier_does_not_receive_primary_caption_confidence_or_reference():
    prompt = checker_prompt('cash_left_unattended', [0., 2., 4., 6.])
    assert 'walks away' in prompt and 'uncertain' in prompt
    assert '.mov' not in prompt and 'confidence' not in prompt and 'organizer' not in prompt


def test_checker_grammar_accepts_uncertainty_without_a_conflicting_verdict():
    from lmformatenforcer import JsonSchemaParser, CharacterLevelParserConfig
    schema = checker_schema(4)
    parser = JsonSchemaParser(schema, config=CharacterLevelParserConfig(force_json_field_order=True))
    raw = json.dumps({'checks': ['visible', 'uncertain', 'absent'], 'evidence_indices': []})
    for char in raw:
        assert char in parser.get_allowed_characters()
        parser = parser.add_character(char)
    assert parser.can_end()


def test_verifier_context_includes_neighbouring_after_state_at_same_image_budget():
    frames = [{'frame_index': i, 'timestamp_sec': float(i), 'sha256': str(i)} for i in range(14)]
    window = {'frames': frames[:6], 'response': {'timecode_sec': 5.}}
    windows = [window, {'frames': frames[6:]}]
    chosen = context_frames(window, windows, 20.)
    assert len(chosen) == 6 and frames[5] in chosen and frames[6] in chosen
    assert chosen[0] == frames[0] and chosen[-1] == frames[13]
    assert all(f in frames for f in chosen)
    assert context_frames(window, windows, 0.) == frames[:6]
