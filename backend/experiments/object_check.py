"""Local evidence verifier trial. Never imports labels or filenames into prompts."""
import json

from backend.analyzer.errors import InferenceError
from backend.analyzer.vlm import json_payload


def schema(frame_count):
    indices = {"type": "array", "maxItems": 3,
               "items": {"type": "integer", "enum": list(range(frame_count))}}
    return {"type": "object", "additionalProperties": False,
        "required": ["object_kind", "features", "identity_evidence", "open_evidence", "return_frame"],
        "properties": {
            "object_kind": {"type": "string", "enum": ["room_door", "safe", "cupboard", "uncertain"]},
            "features": {"type": "array", "maxItems": 3, "items": {"type": "string",
                "enum": ["human_passage", "storage_cavity", "security_hardware"]}},
            "identity_evidence": indices, "open_evidence": indices,
            "return_frame": {"anyOf": [{"type": "integer", "enum": list(range(frame_count))},
                                        {"type": "null"}]}}}


def prompt(timestamps, anchor, *, arrival_movement=False):
    return_criterion = (
        "return_frame is the frame showing the person's arrival at the workstation after visibly "
        "moving from the doorway toward it, followed by resuming work there. The earlier departure "
        "need not be shown, but the movement back MUST be visible. Null if this transition is absent. "
        if arrival_movement else
        "return_frame is the frame visibly showing the person RETURN to the workstation AFTER being "
        "away; null if this transition is not shown. "
    )
    return (
        "Identify the hinged object involved near the indicated anchor time, from these silent frames. "
        "Do NOT assume it is a safe because this is a bank. First inspect physical structure. "
        "room_door: floor-connected human passage in a wall/partition; a person passes THROUGH the opening. "
        "safe: bounded storage cavity PLUS visible security construction (reinforced edge, locking bolts "
        "or recognizable safe lock). cupboard: ordinary storage cabinet without identifiable safe hardware. "
        "A swinging rectangular panel/handle/color alone identifies neither safe nor room door. "
        "If unclear choose uncertain. Never use screen coordinates or person identity. "
        "For features select only visibly supported facts about the SAME object: human_passage, "
        "storage_cavity, security_hardware. identity_evidence lists supporting frame indices. "
        "open_evidence lists frames where this SAME object's opening is visibly open, not just occluded. "
        + return_criterion +
        "Sitting/standing at a desk alone is insufficient. "
        "Frame index/time: " + json.dumps(list(enumerate(timestamps))) + ". Anchor time: " + str(anchor)
        + ". Return only JSON: object_kind, features, identity_evidence, open_evidence, return_frame. "
        "No prose or legal conclusion. Never invent an invisible feature."
    )


def parse(raw, frame_count):
    try:
        data = json.loads(json_payload(raw))
        if set(data) != set(schema(frame_count)['required']):
            raise ValueError('Wrong object-check fields')
        if data['object_kind'] not in {'room_door', 'safe', 'cupboard', 'uncertain'}:
            raise ValueError('Unknown object kind')
        features = data['features']
        if not isinstance(features, list) or len(features) > 3 or any(
                not isinstance(x, str) or x not in {'human_passage', 'storage_cavity', 'security_hardware'} for x in features):
            raise ValueError('Invalid features')
        for key in ('identity_evidence', 'open_evidence'):
            ids = data[key]
            if not isinstance(ids, list) or len(ids) > 3 or any(type(x) is not int or not 0 <= x < frame_count for x in ids) or len(set(ids)) != len(ids):
                raise ValueError('Invalid object evidence')
        ret = data['return_frame']
        if ret is not None and (type(ret) is not int or not 0 <= ret < frame_count):
            raise ValueError('Invalid return frame')
        return data
    except (ValueError, TypeError, KeyError) as exc:
        raise InferenceError('Invalid object verification response') from exc


def supported_type(data, timestamps):
    if not data['identity_evidence'] or len(data['open_evidence']) < 2:
        return None
    span = max(timestamps[i] for i in data['open_evidence']) - min(timestamps[i] for i in data['open_evidence'])
    if span < 1:
        return None
    features = set(data['features'])
    if data['object_kind'] == 'safe' and {'storage_cavity', 'security_hardware'} <= features and 'human_passage' not in features:
        return 'safe_door_open'
    ret = data['return_frame']
    if (data['object_kind'] == 'room_door' and 'human_passage' in features and ret is not None
            and any(timestamps[i] >= timestamps[ret] + 1 for i in data['open_evidence'])):
        return 'cash_room_door_left_open'
    return None
