"""Offline second-stage experiment on actual cached VLM windows. Local AI only.

Timing here is an additive estimate; a final public-pipeline run is still required.
Reference labels are used only by the metrics after inference.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import time

from backend.analyzer.contracts import EvidenceFrame, Finding
from backend.analyzer.errors import AnalyzerError, InferenceError
from backend.analyzer.labels import read_labels
from backend.analyzer.metrics import compute_metrics
from backend.analyzer.pipeline import deduplicate
from backend.analyzer.video import VideoReader
from backend.analyzer.vlm import json_payload
from backend.scripts.diagnose_quality import digest
from backend.scripts.evaluate import _save, verify_findings


CHECKS = {
    'cash_split_transfer': ['Two distinct portions of identifiable banknotes are visible.',
        'One portion is handed to another person.',
        'A different portion visibly remains separately after the handover; mere counting/one-bundle handover is insufficient.'],
    'cash_left_unattended': ['Identifiable banknotes are on the working surface.',
        'The person actually walks away from that workstation, rather than reaching/turning aside.',
        'The banknotes visibly remain after that departure.'],
    'key_left_in_safe': ['A physical key is clearly resolved, not a handle/reflection.',
        'The key is visibly inserted in a lock.', 'The lock belongs to an identifiable safe, not a room door.'],
    'items_left_unattended': ['Identify a loose movable item used in the operation, not fixed equipment/furniture.',
        'The person actually walks away from the workstation, not merely reaches aside.',
        'That same loose item visibly remains after departure.'],
    'third_person_at_counter': ['Three distinct people are simultaneously visible at the same operation surface.',
        'All three visibly participate in that interaction, rather than stand/pass in the background.',
        'Multiple frames establish this interaction; do not infer any person is unauthorized.'],
    'safe_door_open': ['The object is structurally a safe with a visible open door/cavity, not a room door/drawer.',
        'The person finishes interacting with it and visibly steps away.',
        'The same safe visibly remains open afterwards; ordinary opening while actively using it is insufficient.'],
    'documents_left_unattended': ['Identifiable loose paper documents are on the operation surface.',
        'The person actually leaves that workstation, not merely turns/reaches.',
        'The papers visibly remain after departure.'],
    'cash_room_door_left_open': ['The opening is a room entrance door, not a safe.',
        'A person visibly returns to a workstation.', 'That room door remains visibly open after the return.'],
    'customer_leaves_during_count': ['Two participants visibly interact in a cash exchange.',
        'One participant actually walks away during that operation.',
        'The other visibly continues counting banknotes after departure.'],
    'cash_not_secured_between_clients': ['Identifiable banknotes are present in the earlier interaction.',
        'The operation transitions to a different participant.',
        'The earlier banknotes visibly remain during the next operation; do not guess ownership or mixing.'],
    'personal_items_on_counter': ['A phone, personal bag or other everyday movable object is clearly identifiable.',
        'It lies on the same surface visibly used for the cash operation.',
        'The claim is supported by the image, not guessed from a tiny blur or equipment.'],
    'drinks_at_workstation': ['A cup/glass/drink container is clearly identifiable.',
        'It is on the operation work surface rather than in the background.',
        'The object is actually visible, not a reflection or an unidentified cylinder.'],
}


def checker_schema(frame_count):
    return {'type': 'object', 'additionalProperties': False, 'required': ['checks', 'evidence_indices'],
        'properties': {'checks': {'type': 'array', 'minItems': 3, 'maxItems': 3,
            'items': {'type': 'string', 'enum': ['visible', 'absent', 'uncertain']}},
            'evidence_indices': {'type': 'array', 'minItems': 0, 'maxItems': 3,
                'items': {'type': 'integer', 'enum': list(range(frame_count))}}}}


def checker_prompt(kind, timestamps, rule=None):
    rule = rule or {'min_evidence': 1, 'min_context_sec': 0}
    return ('Critically check THREE visual facts in a silent sequence. Do not trust the proposed category. '
        'Use visible for directly shown, absent for clearly not occurring, uncertain for insufficient detail/context. '
        'This is visual evidence checking, not legal guilt. No roles from coordinates, identities, hidden checks, '
        'banknote denominations, ownership or authorization guesses. Ordinary reaching/counting does not prove departure. '
        'Category to test: ' + kind + '. Facts in order: ' + json.dumps(CHECKS[kind]) + '. '
        'Frame indices and source seconds: ' + json.dumps(list(enumerate(timestamps))) + '. '
        f"A supported event needs at least {rule['min_evidence']} selected frames spanning "
        f"at least {rule['min_context_sec']} seconds. More spacing alone is not visual proof. "
        'Return ONLY JSON: checks (exactly three values: visible/absent/uncertain), evidence_indices (0-3 distinct indices). '
        'Only if ALL three facts are visible, select 1-3 frames jointly proving them; put the event/outcome frame FIRST, '
        'then its earlier/later context. Otherwise use []. Never choose unrelated images merely to meet a duration.')


def parse_check(raw, timestamps, rule):
    try:
        data = json.loads(json_payload(raw))
        if set(data) != {'checks', 'evidence_indices'}:
            raise ValueError('Unexpected verifier fields')
        checks, ids = data['checks'], data['evidence_indices']
        if not isinstance(checks, list) or len(checks) != 3 or any(c not in {'visible', 'absent', 'uncertain'} for c in checks):
            raise ValueError('Invalid visual facts')
        if (not isinstance(ids, list) or len(ids) > 3 or len(set(ids)) != len(ids)
                or any(type(i) is not int or not 0 <= i < len(timestamps) for i in ids)):
            raise ValueError('Invalid verifier evidence')
        if checks != ['visible'] * 3:
            return data, 'insufficient_information' if 'uncertain' in checks else 'not_supported'
        if len(ids) < rule['min_evidence'] or max(timestamps[i] for i in ids) - min(timestamps[i] for i in ids) < rule['min_context_sec']:
            return data, 'insufficient_temporal_evidence'
        return data, 'supported'
    except (ValueError, TypeError, KeyError) as exc:
        raise InferenceError(f'Invalid verification JSON: {exc}') from exc


def context_frames(window, windows, context_sec, limit=6):
    """Choose actual neighbouring primary frames without labels or invented times."""
    if context_sec <= 0:
        return window['frames']
    anchor = window['response']['timecode_sec']
    bank = {f['frame_index']: f for w in windows for f in w['frames']
            if abs(f['timestamp_sec'] - anchor) <= context_sec / 2}
    ordered = sorted(bank.values(), key=lambda f: f['timestamp_sec'])
    if len(ordered) <= limit:
        return ordered
    nearest = min(ordered, key=lambda f: abs(f['timestamp_sec'] - anchor))
    chosen = {ordered[0]['frame_index'], ordered[-1]['frame_index'], nearest['frame_index']}
    for side in ([f for f in ordered if f['timestamp_sec'] < anchor],
                 [f for f in ordered if f['timestamp_sec'] > anchor]):
        if side:
            chosen.add(min(side, key=lambda f: abs(f['timestamp_sec'] - anchor))['frame_index'])
    while len(chosen) < limit:
        selected = [f for f in ordered if f['frame_index'] in chosen]
        next_frame = max((f for f in ordered if f['frame_index'] not in chosen),
                         key=lambda f: min(abs(f['timestamp_sec'] - g['timestamp_sec']) for g in selected))
        chosen.add(next_frame['frame_index'])
    return [f for f in ordered if f['frame_index'] in chosen]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Completed pilot/full report with actual window audits')
    parser.add_argument('--id', required=True)
    parser.add_argument('--videos-dir', default='dataset/videos')
    parser.add_argument('--labels', default='backend/data/labels.csv')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--context-sec', type=float, default=0.)
    args = parser.parse_args()
    if not args.id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in args.id):
        parser.error('Invalid experiment id')
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    if digest(args.labels) != source['run_inputs']['labels_sha256']:
        raise ValueError('Labels differ from the primary experiment')
    config = source['run_inputs']['config']
    output = Path('backend/data/reports/quality') / args.id
    if (output / 'checkpoint.json').exists() and not args.resume:
        parser.error('Experiment already has results: use --resume with unchanged inputs or a new id')
    if not 0 <= args.context_sec <= 30:
        parser.error('context-sec must be in [0, 30]')
    inputs = {'source_sha256': digest(args.source), 'code_sha256': digest(__file__), 'context_sec': args.context_sec,
        'source_fingerprint': source['fingerprint'], 'checker': CHECKS,
        'notice': 'Cached actual primary inference + new real local verification. Timing is estimated.'}
    manifest = output / 'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8')) != inputs:
        raise ValueError('Verification experiment changed')
    _save(manifest, inputs)
    snapshot = output / 'verify_candidates.py'
    if snapshot.exists() and digest(snapshot) != inputs['code_sha256']:
        raise ValueError('Verifier source snapshot changed')
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    for variable, name in (('HF_HOME', 'huggingface'), ('TORCH_HOME', 'torch'), ('CUDA_CACHE_PATH', 'cuda')):
        os.environ[variable] = str(Path(config['temp_dir']) / 'runtime-cache' / name)
    from backend.analyzer.network_guard import install_network_guard
    install_network_guard()
    from backend.analyzer.vlm import LocalTransformersVLM
    vlm = LocalTransformersVLM(config['vlm_dir'], config['device'], trace_dir=output / 'diagnostics', release_cuda_cache=True)
    state_path = output / 'checkpoint.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if args.resume and state_path.exists() else {
        'predictions': {}, 'window_checks': {}, 'timings': {}, 'errors': []}
    for video, windows in source['window_audits'].items():
        if video in state['predictions'] or any(e['video'] == video for e in state['errors']):
            continue
        started = time.monotonic()
        needed = {f['frame_index']: f['sha256'] for w in windows for f in w['frames']}
        images_by_index = {}
        with VideoReader(Path(args.videos_dir) / video) as reader:
            for frame in reader.sample(config['sample_fps'], config['max_side'], config['max_sample_frames']):
                if frame.frame_index in needed:
                    data = frame.jpeg()
                    if hashlib.sha256(data).hexdigest() != needed[frame.frame_index]:
                        raise ValueError('Verifier input differs from actual primary frames')
                    images_by_index[frame.frame_index] = data
        findings, checks = [], []
        try:
            for window in windows:
                response = window.get('response', {})
                if not response.get('has_violation') or response['confidence'] < config['confidence_threshold']:
                    continue
                kind = response['violation_type']
                if kind not in CHECKS:
                    continue
                frames = context_frames(window, windows, args.context_sec)
                timestamps = [f['timestamp_sec'] for f in frames]
                images = [images_by_index[f['frame_index']] for f in frames]
                vlm.set_response_schema(checker_schema(len(images)))
                rule = source['run_inputs']['policy'][kind]
                raw = vlm.infer(images, checker_prompt(kind, timestamps, rule), config['vlm_timeout_sec'])
                data, verdict = parse_check(raw, timestamps, rule)
                checks.append({'window': window['window'], 'type': kind, 'result': verdict,
                               'frames': frames, **data})
                if verdict == 'supported':
                    ids = data['evidence_indices']
                    findings.append(Finding(kind, timestamps[ids[0]], response['observed_action'], response['confidence'],
                        [EvidenceFrame(timestamps[i], images[i]) for i in ids]))
            state['predictions'][video] = verify_findings(deduplicate(findings, config['dedup_sec']), source['run_inputs']['catalog'])
            state['window_checks'][video] = checks
            seconds = time.monotonic() - started
            state['timings'][video] = {'verification_sec': seconds,
                'estimated_public_wall_sec': source['timings'][video]['wall_sec'] + seconds,
                'notice': 'Estimate excludes second model initialization, since public worker reuses its VLM.'}
        except AnalyzerError as exc:
            state['errors'].append({'video': video, 'error': type(exc).__name__, 'message': str(exc)})
        _save(state_path, state)
        print(json.dumps({'completed': len(state['predictions']), 'errors': len(state['errors']), 'video': video,
            'findings': len(state['predictions'][video]) if video in state['predictions'] else None}, ensure_ascii=True), flush=True)
    labels = read_labels(args.labels)
    if digest(args.labels) != source['run_inputs']['labels_sha256']:
        raise ValueError('Labels changed while the verifier was running')
    report = {**state, 'metrics': compute_metrics(labels, state['predictions']), 'run_inputs': inputs,
        'verification_outcomes': dict(Counter(c['result'] for cs in state['window_checks'].values() for c in cs)),
        'notice': inputs['notice']}
    _save(output / 'results.json', report)
    print(json.dumps({'evaluated': len(state['predictions']), 'errors': len(state['errors']),
        'video_detection': report['metrics']['video_detection'], 'localization': report['metrics']['localization'],
        'verification_outcomes': report['verification_outcomes']}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
