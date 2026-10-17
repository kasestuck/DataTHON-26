import json

import pytest

from backend.analyzer.errors import InferenceError
from backend.experiments.object_check import parse, prompt, supported_type


def facts(**changes):
    return dict(object_kind='safe', features=['storage_cavity','security_hardware'],
                identity_evidence=[0,1], open_evidence=[0,1], return_frame=None, **changes)


def test_safe_requires_positive_structure_and_no_passage():
    data = facts()
    assert supported_type(data, [0.,2.]) == 'safe_door_open'
    data['features'] = ['storage_cavity']
    assert supported_type(data, [0.,2.]) is None
    data['features'] = ['storage_cavity','security_hardware','human_passage']
    assert supported_type(data, [0.,2.]) is None


def test_room_requires_return_and_later_open_frame():
    data = facts()
    data.update(object_kind='room_door', features=['human_passage'])
    assert supported_type(data, [0.,2.]) is None
    data['return_frame'] = 0
    assert supported_type(data, [0.,2.]) == 'cash_room_door_left_open'
    data['return_frame'] = 1
    assert supported_type(data, [0.,2.]) is None


def test_unknown_object_is_not_automatically_relabelled():
    data = facts()
    data['object_kind'] = 'uncertain'
    assert supported_type(data, [0.,2.]) is None


@pytest.mark.parametrize('field,value', [('return_frame',True),('open_evidence',[0,0]),('identity_evidence',[3]),('features',['imaginary'])])
def test_invalid_structure_is_error(field,value):
    data = facts()
    data[field] = value
    with pytest.raises(InferenceError):
        parse(json.dumps(data),2)


def test_prompt_uses_only_source_frame_times():
    text = prompt([0.25,1.75,3.25],1.75)
    assert '1.75' in text and 'safe_door_open' not in text
    assert 'X-1' not in text and 'X-4' not in text
