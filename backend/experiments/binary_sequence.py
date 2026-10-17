"""Two-stage local sequence experiment, isolated from production inference.

No labels, filenames, source observations or reference timestamps enter prompts.
The first stage tests a visible suspicious sequence; only supported candidates
are classified. Production policy, evidence checks and Finding stay unchanged.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from backend.analyzer.catalog import load_catalog
from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.errors import AnalysisTimeoutError, InferenceError
from backend.analyzer.labels import parse_verdict, read_labels
from backend.analyzer.metrics import compute_metrics
from backend.analyzer.pipeline import Analyzer, _supervise, _worker_error
from backend.analyzer.policy import load_policy
from backend.analyzer.vlm import generation_schema, json_payload
from backend.scripts.evaluate import _save, verify_findings


def binary_schema(frame_count):
    return {"type": "object", "additionalProperties": False,
        "required": ["observation", "decision", "evidence_indices"], "properties": {
            "observation": {"type": "string", "minLength": 1, "maxLength": 80},
            "decision": {"type": "string", "enum": ["supported", "routine", "uncertain"]},
            "evidence_indices": {"type": "array", "minItems": 0, "maxItems": 3,
                "items": {"type": "integer", "enum": list(range(frame_count))}}}}


def binary_prompt(timestamps):
    return (
        "Inspect silent banking video frames in chronological order. First decide whether a visible "
        "sequence supports a potentially unsafe action, BEFORE assigning any event category. "
        "Compare BEFORE, ACTION and AFTER. Supported examples: a person visibly leaves and cash or "
        "documents remain exposed afterwards; a clearly identified safe or room entrance is left open "
        "after work resumes elsewhere; cash remains across two visibly distinct transactions. "
        "Require identifiable objects and an observed change/outcome. Mere presence is not evidence. "
        "NORMAL counterexamples: counting or handing over cash during ongoing service; temporarily "
        "placing banknotes on a desk; opening a safe while actively using it; a passing person; "
        "a screen lit during work. These alone are routine, not supported. A key, cup or phone merely "
        "visible is not proof of a violation. Do not infer identity, authority, ownership, hidden checks, "
        "screen lock state, or intent. Unclear objects or missing before/after context mean uncertain. "
        "This is a visual risk hypothesis, never legal proof. Source frame indices and seconds: "
        + json.dumps(list(enumerate(timestamps))) + ". Return only JSON in order: observation "
        "(one short Russian sentence, at most 12 words), decision (supported/routine/uncertain), "
        "evidence_indices. For supported select 2-3 DISTINCT frames showing the sequence, outcome FIRST; "
        "otherwise select []. All selected frames must actually support the SAME sequence."
    )


def parse_binary(raw, frame_count):
    try:
        data = json.loads(json_payload(raw))
        if not isinstance(data, dict) or set(data) != {"observation", "decision", "evidence_indices"}:
            raise ValueError("Unexpected binary fields")
        ids = data["evidence_indices"]
        if (not isinstance(data["observation"], str) or not data["observation"].strip()
                or data["decision"] not in {"supported", "routine", "uncertain"}
                or not isinstance(ids, list) or len(ids) > 3
                or any(type(i) is not int or not 0 <= i < frame_count for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("Invalid binary decision or evidence")
        if (data["decision"] == "supported" and len(ids) < 2
                or data["decision"] != "supported" and ids):
            raise ValueError("Binary decision contradicts evidence")
        return data
    except (ValueError, TypeError, KeyError) as exc:
        raise InferenceError(f"Invalid experimental binary JSON: {exc}") from exc


class SequenceGate:
    is_mock = False

    def __init__(self, vlm, catalog, policy):
        self.vlm, self.catalog, self.policy = vlm, catalog, policy
        self.windows, self.timestamps, self.context = [], [], {}

    def set_response_schema(self, schema):
        self.timestamps = schema["properties"]["timecode_sec"]["anyOf"][0]["enum"]

    def set_window_context(self, **context):
        self.context = context

    def infer(self, images, prompt, timeout_sec):
        # The classifier's original prompt is deliberately not used: it equates
        # ordinary catalog matches with a positive video verdict.
        deadline = time.monotonic() + timeout_sec
        self.vlm.set_window_context(**self.context)
        self.vlm.set_response_schema(binary_schema(len(images)))
        started = time.monotonic()
        data = parse_binary(self.vlm.infer(images, binary_prompt(self.timestamps), timeout_sec), len(images))
        record = {"window": self.context.get("window"), "binary": data,
            "timestamps": list(self.timestamps), "binary_sec": time.monotonic() - started}
        self.windows.append(record)
        if data["decision"] != "supported":
            return json.dumps(dict(observed_action=data["observation"], has_violation=False,
                violation_type=None, timecode_sec=None, confidence=0., evidence_indices=[]), ensure_ascii=False)
        remaining = deadline - time.monotonic()
        if remaining <= 2:
            raise AnalysisTimeoutError("Experimental classification has no remaining window budget")
        self.vlm.set_response_schema(generation_schema(self.catalog, self.timestamps))
        criteria = [{"id": key, "criteria": value["description"],
            "min_frames": self.policy[key]["min_evidence"], "min_span_sec": self.policy[key]["min_context_sec"]}
            for key, value in self.catalog.items()]
        # Both calls receive the same images. No first-stage prose is fed back
        # as fact; the classifier must independently inspect the sequence.
        classification = (
            "Classify ONLY a suspicious sequence demonstrated by these silent frames. Inspect beginning "
            "and outcome independently. Ordinary counting, handover, presence or opening during active work "
            "alone is insufficient. No roles, ownership, unseen checks or legal guilt. "
            "If unsupported or not classifiable, return false and null type/time with []. "
            "Catalog criteria and evidence minima: " + json.dumps(criteria, ensure_ascii=False)
            + ". Frame indices and absolute seconds: " + json.dumps(list(enumerate(self.timestamps)))
            + ". Return JSON in order: observed_action (short Russian sentence, at most 12 words), "
            "violation_type (catalog id or null), timecode_sec (EXACT time of a selected proof frame or null), "
            "evidence_indices (1-3 distinct frame indices supporting the SAME sequence, meeting minima), "
            "confidence (0..1), has_violation (boolean). Never select unrelated frames to meet a span."
        )
        started = time.monotonic()
        raw = self.vlm.infer(images, classification, remaining)
        record["classification_sec"] = time.monotonic() - started
        return raw


def _worker(connection, video_path, config, deadline):
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
    try:
        from backend.analyzer.network_guard import install_network_guard
        install_network_guard()
        import torch
        from backend.analyzer.detector import LocalRTDetr
        from backend.analyzer.vlm import LocalTransformersVLM
        torch.set_num_threads(1)
        torch.cuda.reset_peak_memory_stats()
        started = time.monotonic()
        catalog = load_catalog(config.catalog_path)
        policy = load_policy(catalog, config.catalog_path, config.policy_path)
        allowed = {key: value for key, value in catalog.items() if policy[key]["enabled"]}
        detector = LocalRTDetr(config.detector_dir, config.device)
        vlm = LocalTransformersVLM(config.vlm_dir, config.device,
            trace_dir=Path(config.temp_dir) / "diagnostics", release_cuda_cache=True)
        gate = SequenceGate(vlm, allowed, policy)
        loaded = time.monotonic()
        report = Analyzer(detector, gate, config, deadline=deadline).analyze(video_path)
        report.timings["model_load_sec"] = loaded - started
        report.peak_vram_bytes = torch.cuda.max_memory_allocated()
        connection.send((True, (report, gate.windows)))
    except Exception as exc:
        connection.send(_worker_error(exc))
    finally:
        connection.close()


def run_video(video_path, config):
    started = time.monotonic()
    report, windows = _supervise(_worker, (video_path, config, started + config.timeout_sec), config.timeout_sec)
    report.timings["wall_sec"] = time.monotonic() - started
    return report, windows


def select_pilot(labels, baseline):
    """One video per baseline outcome from each existing dev/control partition.

    Used only for evaluation selection, never in a model call. All source videos
    have already been inspected; this is not an unseen generalization estimate.
    """
    by_video = {r["video"]: r for r in labels}
    chosen = {}
    for subset in ("diagnostic", "control"):
        groups = {}
        for video in baseline["run_inputs"]["split"][subset]:
            truth, predicted = parse_verdict(by_video[video]["has_violation"]), bool(baseline["predictions"][video])
            group = ("TP" if predicted else "FN") if truth else ("FP" if predicted else "TN")
            groups.setdefault(group, video)
        chosen[subset] = [groups[g] for g in ("TP", "FN", "FP", "TN") if g in groups]
    return chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--baseline", default="backend/data/reports/quality/stable_anchor_cache_v7/full.json")
    parser.add_argument("--labels", default="backend/data/labels.csv")
    parser.add_argument("--videos-dir", default="dataset/videos")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in args.id):
        parser.error("Invalid experiment id")
    output = Path("backend/data/reports/official_evaluation") / args.id
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    labels = read_labels(args.labels)
    split = select_pilot(labels, baseline)
    config = replace(AnalyzerConfig.from_env(), record_window_audit=True, temp_dir=str(output / "runtime"))
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest = {"experiment": args.id, "config": asdict(config), "split": split,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "code": {str(p): sha(p) for p in [Path(__file__), *sorted(Path('backend/analyzer').glob('*.py'))]},
        "labels_sha256": sha(args.labels), "baseline_sha256": sha(args.baseline),
        "catalog_sha256": sha(config.catalog_path), "policy_sha256": sha(Path(config.catalog_path).with_name('violation_type_policy.json')),
        "video_hashes": {v: sha(Path(args.videos_dir) / v) for vs in split.values() for v in vs},
        "scope": "Previously inspected development videos, not jury data or unseen camera; no training."}
    manifest_path = output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
        raise ValueError("Frozen experiment inputs changed; use a new experiment id")
    state_path = output / "results.json"
    if state_path.exists() and not args.resume:
        parser.error("Results exist; use --resume with identical inputs")
    _save(manifest_path, manifest)
    state = json.loads(state_path.read_text(encoding='utf-8')) if args.resume and state_path.exists() else {
        "predictions": {}, "timings": {}, "windows": {}, "errors": [], "peak_vram_bytes": 0}
    for subset, videos in split.items():
        for video in videos:
            if video in state['predictions'] or any(e['video'] == video for e in state['errors']):
                continue
            started = time.monotonic()
            try:
                report, windows = run_video(str((Path(args.videos_dir) / video).resolve()), config)
                state['predictions'][video] = verify_findings(report.findings, load_catalog(config.catalog_path))
                state['timings'][video] = report.timings
                state['windows'][video] = windows
                state['peak_vram_bytes'] = max(state['peak_vram_bytes'], report.peak_vram_bytes or 0)
            except Exception as exc:
                state['errors'].append(dict(video=video, error_type=type(exc).__name__, message=str(exc),
                    wall_sec=time.monotonic() - started))
            actual = state['predictions']
            state['metrics'] = compute_metrics(labels, actual)
            state['baseline_same_videos'] = compute_metrics(labels, {v: baseline['predictions'][v] for v in actual})
            state['manifest'] = manifest
            _save(state_path, state)
            print(json.dumps({'subset': subset, 'completed': len(actual), 'errors': len(state['errors']),
                'last_sec': time.monotonic() - started, 'video_detection': state['metrics']['video_detection']}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
