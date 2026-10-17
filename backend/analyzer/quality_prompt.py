"""Experimental sequence-focused instructions. No video names or reference labels."""
import itertools
import json


VISUAL_CHECKS = {
    "cash_split_transfer": "See two distinct portions: one is handed over, another visibly kept separately AFTER the handover. Mere counting or handing over a bundle is insufficient.",
    "cash_left_unattended": "See the person leave AND identifiable banknotes remain on the work surface afterwards. A person reaching aside while continuing to handle cash is insufficient.",
    "key_left_in_safe": "Clearly resolve a physical key inserted into the lock of an identifiable safe. A dark spot, handle or reflection is insufficient.",
    "items_left_unattended": "See a person leave AND identifiable items remain afterwards. Use the more specific cash/document category when appropriate.",
    "third_person_at_counter": "Resolve three people actively interacting at the SAME transaction surface in multiple frames. Do not label passers-by or infer unauthorized presence.",
    "safe_door_open": "Identify a SAFE by its physical structure and open cavity in multiple frames. Distinguish it from room doors, drawers, bags and partitions. Do not infer an open safe from a person bending down.",
    "documents_left_unattended": "See a person leave AND identifiable paper documents remain on the work surface afterwards. Paper temporarily present during ongoing work is insufficient.",
    "cash_room_door_left_open": "See the person RETURN to the workstation AND the ROOM entrance door remain visibly open afterwards. Opening a door alone is insufficient. Do not confuse it with a safe.",
    "customer_leaves_during_count": "See one participant leave after participating in cash exchange WHILE the other visibly continues counting banknotes. Merely walking or reaching away is insufficient.",
    "cash_not_secured_between_clients": "Track identifiable banknotes across THREE phases: earlier transaction, transition to a DIFFERENT participant, and next transaction while the earlier cash remains. Do not assume physical mixing or ownership from position.",
    "personal_items_on_counter": "Clearly identify a phone, personal bag or similar everyday object on the cash-working surface. Do not guess small ambiguous objects or ownership.",
    "drinks_at_workstation": "Clearly identify a cup/glass/drink container on the work surface. An unidentified cylindrical object is insufficient.",
}


def evidence_options(timestamps, rule):
    """Valid spans are computed from supplied frames, without adding evidence."""
    size = rule.get("min_evidence", 1)
    valid = [list(ids) for ids in itertools.combinations(range(len(timestamps)), size)
             if timestamps[ids[-1]] - timestamps[ids[0]] >= rule.get("min_context_sec", 0)]
    # A compact set of dispersed alternatives; these are options, not proof.
    if len(valid) > 4:
        valid = [valid[round(i * (len(valid) - 1) / 3)] for i in range(4)]
    return valid


def build_sequence_prompt(catalog, timestamps, policy=None):
    kinds = []
    for key, value in catalog.items():
        rule = (policy or {}).get(key, {"min_evidence": 1, "min_context_sec": 0})
        kinds.append({"id": key, "visible_test": VISUAL_CHECKS.get(key, value["description"]),
            "minimum_frames": rule["min_evidence"], "minimum_span_seconds": rule["min_context_sec"],
            "example_temporally_valid_sets": evidence_options(timestamps, rule)})
    return (
        "Inspect a SILENT video sequence. Decide if a catalog event is actually demonstrated. "
        "First compare the BEFORE state, visible ACTION, and AFTER state. "
        "Ordinary counting, handing over cash, reaching, passing people or opening a door alone "
        "does not establish a more specific sequence. Do not guess tiny objects. "
        "A catalog event is a visual hypothesis, never legal guilt. No face identification, "
        "roles from coordinates, hidden intentions, banknote denominations or unseen checks. "
        "If the required object/action/outcome cannot be seen, say insufficient information and return false. "
        "If none occurs, say no supported event and return false. "
        "Only if all visible tests for one category are demonstrated return true. "
        "EACH selected evidence frame must visibly support that SAME event. Example index sets "
        "only meet time requirements; choose them ONLY when their images really prove the event. "
        "For a sequence choose beginning AND outcome; for a transaction transition choose all THREE phases. "
        "Choose the event time from a selected evidence frame where the action/outcome becomes visible, "
        "not automatically the first frame or the middle of the whole window. "
        "Indexed source frames (index, absolute seconds): " + json.dumps(list(enumerate(timestamps))) + ". "
        "Allowed catalog: " + json.dumps(kinds, ensure_ascii=False) + ". "
        "Return ONE JSON object in this order: observed_action (one short Russian sentence of at most 12 words), "
        "violation_type (exact allowed id or null), timecode_sec (supplied evidence frame time or null), "
        "evidence_indices (1-3 distinct 0-based indices proving the same event, meeting its minimum count/span), "
        "confidence (0..1, do not assume certainty), has_violation (boolean). "
        "For no event OR insufficient information: null type, null time, [], has_violation=false."
    )
