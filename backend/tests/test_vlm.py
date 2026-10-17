import json

import pytest

from backend.analyzer.errors import AnalysisTimeoutError, InferenceError, ModelNotConfiguredError
from backend.analyzer.vlm import (LocalTransformersVLM, MockVLM, build_prompt, validate_response,
                                  complete_json_object, generation_schema, normalize_negative_evidence,
                                  grammar_logits_processor)

CATALOG = {"synthetic": {"name": "Synthetic", "description": "Synthetic test condition"}}


def response():
    return dict(observed_action="synthetic action", has_violation=True, violation_type="synthetic",
        timecode_sec=1., confidence=.9, evidence_indices=[0, 1])


def test_json_and_empty_catalog():
    result = validate_response(json.dumps(response()), CATALOG, 4)
    assert result.has_violation
    with pytest.raises(InferenceError, match="confirmed"):
        validate_response(response(), {}, 4)
    assert not validate_response(MockVLM().infer([], "", 1), {}, 4).has_violation
    assert "empty catalog" in build_prompt({}, [0, 1, 2, 3])
    assert validate_response("```json\n" + json.dumps(response()) + "\n```", CATALOG, 4).has_violation
    integer_time = response()
    integer_time["timecode_sec"] = 1
    assert type(validate_response(integer_time, CATALOG, 4).timecode_sec) is float


@pytest.mark.parametrize("field,value", [("confidence", True), ("confidence", float("nan")),
    ("confidence", 2), ("has_violation", "true"), ("evidence_indices", [0, 0]),
    ("evidence_indices", [True]), ("evidence_indices", [9]), ("timecode_sec", -1),
    ("violation_type", "unknown"), ("observed_action", ""), ("extra", "bad")])
def test_reject_invalid(field, value):
    data = response()
    data[field] = value
    with pytest.raises(InferenceError):
        validate_response(data, CATALOG, 4)


def test_invalid_json_and_negative_semantics():
    with pytest.raises(InferenceError):
        validate_response("```json\n{}\n```", {}, 4)
    data = response()
    data["has_violation"] = False
    with pytest.raises(InferenceError):
        validate_response(data, CATALOG, 4)
    with pytest.raises(AnalysisTimeoutError):
        MockVLM().infer([], "", 0)


def test_local_missing_and_restricted_model(tmp_path):
    with pytest.raises(ModelNotConfiguredError):
        LocalTransformersVLM(tmp_path / "missing")
    (tmp_path / "config.json").write_text('{"model_type":"qwen2_5_vl"}', encoding="utf-8")
    with pytest.raises(ModelNotConfiguredError, match="approved"):
        LocalTransformersVLM(tmp_path)


@pytest.mark.parametrize("raw,complete", [('{"text":"a } in a string"}', True),
    ('```json\n{"a":1}', True), ('{"a":', False), ('{"a":"unfinished', False),
    ('{"a":1} extra text', False), ('[1,2]', False)])
def test_json_generation_stops_only_after_complete_object(raw, complete):
    assert complete_json_object(raw) is complete


def test_token_grammar_enforces_ids_times_and_structure():
    pytest.importorskip("lmformatenforcer", reason="Optional local model dependency")
    from lmformatenforcer import JsonSchemaParser, CharacterLevelParserConfig

    schema = generation_schema(CATALOG, [0., 1., 2., 3.])
    config = CharacterLevelParserConfig(force_json_field_order=True)
    config.alphabet += "".join(chr(i) for i in range(0x400, 0x500))

    def accepted(data):
        parser = JsonSchemaParser(schema, config=config)
        ordered = {key: data[key] for key in schema["required"] if key in data}
        ordered.update({k: v for k, v in data.items() if k not in ordered})
        for character in json.dumps(ordered, ensure_ascii=False):
            if character not in parser.get_allowed_characters():
                return False
            parser = parser.add_character(character)
        return parser.can_end()

    data = dict(has_violation=True, violation_type="synthetic", timecode_sec=1., confidence=.9,
                evidence_indices=[0, 1], observed_action="Синтетическое действие")
    assert accepted(data)
    assert not accepted({**data, "violation_type": "invented"})
    assert not accepted({**data, "timecode_sec": 99.})
    assert not accepted({**data, "evidence_indices": [9]})
    assert not accepted({**data, "observed_action": "я" * 81})
    assert not accepted({**data, "unexpected": True})
    negative = dict(has_violation=False, violation_type=None, timecode_sec=None, confidence=.1,
                    evidence_indices=[], observed_action="Синтетический отрицательный пример")
    assert accepted(negative)


def test_negative_frame_normalization_never_changes_decision_or_conflicts():
    negative = dict(observed_action="Synthetic", has_violation=False, violation_type=None,
                    timecode_sec=None, confidence=0., evidence_indices=[0])
    raw = json.dumps(negative)
    normalized = normalize_negative_evidence(raw, 4)
    assert not validate_response(normalized, CATALOG, 4).has_violation
    for update in ({"has_violation": True}, {"violation_type": "synthetic"}, {"timecode_sec": 1.},
                   {"evidence_indices": [99]}, {"evidence_indices": [[]]}):
        original = json.dumps({**negative, **update})
        assert normalize_negative_evidence(original, 4) == original


def test_cached_grammar_masks_preserve_logits_and_bound_cache():
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    allowed = [1, 3]
    processor = grammar_logits_processor(torch, lambda batch, ids: allowed, cache_size=2)
    logits = torch.tensor([[.1, .5, .9, .3, .2]])
    actual = processor(torch.tensor([[0]]), logits.clone())
    assert actual.argmax().item() == 1
    assert actual[0, 1].item() == logits[0, 1].item()
    assert torch.isneginf(actual[0, [0, 2, 4]]).all()
    processor(torch.tensor([[0, 1]]), logits.clone())
    assert len(processor.masks) == 1
    allowed[:] = [2]
    assert processor(torch.tensor([[0]]), logits.clone()).argmax().item() == 2
    allowed[:] = [4]
    processor(torch.tensor([[0]]), logits.clone())
    assert len(processor.masks) == 2


@pytest.mark.parametrize("raw,complete", [(json.dumps(response()), True), ('{"observed_action":"unfinished', False)])
def test_adapter_generation_boundary_and_private_metadata(monkeypatch, tmp_path, raw, complete):
    """Synthetic generation boundary only; not a real-model quality test."""
    from types import SimpleNamespace
    import io
    import os
    from PIL import Image
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    integration = pytest.importorskip("lmformatenforcer.integrations.transformers")
    monkeypatch.setattr(integration, "build_transformers_prefix_allowed_tokens_fn", lambda *args: None)
    adapter = LocalTransformersVLM.__new__(LocalTransformersVLM)
    adapter.torch, adapter.device = torch, "cpu"
    adapter.trace_dir, adapter.call_number = tmp_path, 0
    adapter.window_context = {}
    adapter.response_schema = generation_schema(CATALOG, [0., 1., 2., 3.])
    adapter.enforcer_tokenizer = None
    adapter.release_cuda_cache = False
    adapter.processor = SimpleNamespace(
        apply_chat_template=lambda *args, **kwargs: transformers.BatchEncoding({"input_ids": torch.tensor([[1]])}),
        batch_decode=lambda *args, **kwargs: [raw],
        tokenizer=SimpleNamespace(decode=lambda *args, **kwargs: raw))

    def generate(**kwargs):
        assert kwargs['max_new_tokens'] == 192 and kwargs['do_sample'] is False
        generated = torch.ones((1, 193), dtype=torch.long)
        kwargs['stopping_criteria'](generated, None)
        return generated

    adapter.model = SimpleNamespace(generate=generate)
    image = io.BytesIO()
    Image.new('RGB', (16, 16)).save(image, format='JPEG')
    if complete:
        assert json.loads(adapter.infer([image.getvalue()], "PRIVATE PROMPT", 10)) == response()
    else:
        with pytest.raises(InferenceError, match="complete JSON"):
            adapter.infer([image.getvalue()], "PRIVATE PROMPT", 10)
    metadata_text = (tmp_path / f'vlm_{os.getpid()}.jsonl').read_text()
    metadata = json.loads(metadata_text)
    assert metadata['complete_json'] is complete
    assert metadata['stop_reason'] == ('complete_json' if complete else 'token_limit')
    assert metadata['frame_count'] == 1 and metadata['frame_sizes'] == [[16, 16]]
    assert 'PRIVATE PROMPT' not in metadata_text and 'observed_action' not in metadata_text
    events = [json.loads(line)['event'] for line in (tmp_path / f'window_events_{os.getpid()}.jsonl').read_text().splitlines()]
    assert events == ['started', 'ended']
