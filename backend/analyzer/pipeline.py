"""Synchronous integration pipeline. Production execution is process-supervised."""
from dataclasses import dataclass, field
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time

from .candidates import CandidateSelector
from .catalog import load_catalog
from .config import AnalyzerConfig
from .contracts import EvidenceFrame, Finding
from .errors import (AnalysisTimeoutError, AnalyzerError, InferenceError,
                     ModelNotConfiguredError, VideoReadError)
from .events import EventExtractor
from .ipc import send_result
from .policy import load_policy
from .video import VideoReader
from .vlm import bind_evidence_timestamp, build_prompt, generation_schema, validate_response


@dataclass
class AnalysisReport:
    findings: list[Finding] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    sampled_frames: int = 0
    candidate_windows: int = 0
    diagnostics: list[str] = field(default_factory=list)
    is_mock: bool = False
    diagnostic_only: bool = False
    peak_vram_bytes: int | None = None
    peak_reserved_vram_bytes: int | None = None
    window_audit: list[dict] = field(default_factory=list)


def deduplicate(findings, tolerance_sec):
    kept = []
    for finding in sorted(findings, key=lambda item: item.confidence, reverse=True):
        if not any(finding.violation_type == old.violation_type and abs(finding.timecode_sec - old.timecode_sec) <= tolerance_sec for old in kept):
            kept.append(finding)
    return sorted(kept, key=lambda item: item.timecode_sec)


class Analyzer:
    """Dependency-injected core. Use analyze_video for hard process deadlines.

    Direct calls check deadlines between operations. Injectable components must
    honor their own timeout. Synthetic components require explicit allow_mock.
    """
    def __init__(self, detector, vlm, config=None, *, allow_mock=False, deadline=None):
        self.config = config or AnalyzerConfig()
        self.detector, self.vlm = detector, vlm
        self.deadline = deadline
        self.is_mock = bool(getattr(detector, "is_mock", True) or getattr(vlm, "is_mock", True))
        if self.is_mock and not allow_mock:
            raise ModelNotConfiguredError("Mock components are allowed only in explicit synthetic tests")
        self.catalog = load_catalog(self.config.catalog_path)
        self.policy = load_policy(self.catalog, self.config.catalog_path, self.config.policy_path)
        self.allowed_catalog = {key: item for key, item in self.catalog.items() if self.policy[key]["enabled"]}
        if self.catalog and not self.allowed_catalog:
            raise ModelNotConfiguredError("Catalog has no enabled visual event types")

    def analyze(self, video_path):
        started = time.monotonic()
        deadline = min(started + self.config.timeout_sec, self.deadline) if self.deadline is not None else started + self.config.timeout_sec
        report = AnalysisReport(is_mock=self.is_mock, diagnostic_only=not bool(self.catalog),
            timings={"extraction_sec": 0., "detection_sec": 0., "vlm_sec": 0.})
        extractor = EventExtractor(self.detector)

        def check_deadline():
            if time.monotonic() >= deadline:
                raise AnalysisTimeoutError("Analysis exceeded its wall time budget; no partial verdict returned")

        def process(candidate):
            if candidate is None:
                return
            check_deadline()
            report.candidate_windows += 1
            images = [frame.jpeg() for frame in candidate.frames]
            timestamps = [frame.timestamp_sec for frame in candidate.frames]
            prompt = build_prompt(self.allowed_catalog, timestamps, self.policy, version=self.config.prompt_version)
            audit = {"window": report.candidate_windows, "selection_reasons": list(candidate.reasons),
                "trigger_timestamp_sec": candidate.trigger_timestamp_sec,
                "frames": [{"frame_index": frame.frame_index, "timestamp_sec": frame.timestamp_sec,
                            "sha256": hashlib.sha256(image).hexdigest()}
                           for frame, image in zip(candidate.frames, images)], "outcome": "inference_pending"}
            if self.config.record_window_audit:
                report.window_audit.append(audit)
            configure_schema = getattr(self.vlm, "set_response_schema", None)
            if configure_schema:
                configure_schema(generation_schema(self.allowed_catalog, timestamps, version=self.config.prompt_version))
            # Keep time for evidence serialization and child-process shutdown.
            # A slower window may use available budget, never renew the job's
            # deadline or return a partial/negative result for unexamined video.
            vlm_deadline = min(deadline - 5., time.monotonic() + self.config.vlm_timeout_sec)
            for attempt in range(2):
                t = time.monotonic()
                if t >= vlm_deadline:
                    raise AnalysisTimeoutError("Insufficient analysis budget for a VLM window")
                configure_context = getattr(self.vlm, "set_window_context", None)
                if configure_context:
                    configure_context(window=report.candidate_windows, attempt=attempt, analysis_deadline=deadline)
                raw = self.vlm.infer(images, prompt, vlm_deadline - t)
                report.timings["vlm_sec"] += time.monotonic() - t
                check_deadline()
                try:
                    response = (bind_evidence_timestamp(raw, self.allowed_catalog, timestamps)
                        if self.config.prompt_version == "frame_bound_v2"
                        else validate_response(raw, self.allowed_catalog, len(images)))
                    break
                except InferenceError:
                    # Private diagnostics remain under the authorized temporary root.
                    log_dir = Path(self.config.temp_dir) / "diagnostics"
                    log_dir.mkdir(parents=True, exist_ok=True)
                    (log_dir / f"invalid_{os.getpid()}_{report.candidate_windows}_{attempt}.json").write_text(
                        json.dumps({"synthetic": Path(video_path).name == "synthetic.mov", "raw": raw},
                                   ensure_ascii=False), encoding="utf-8")
                    if attempt or vlm_deadline - time.monotonic() < 2:
                        raise
                    report.diagnostics.append("schema_retry")
                    prompt += (" Your previous reply failed the JSON schema. Return a fresh valid object. "
                        f"There are EXACTLY {len(images)} frames. Positive evidence_indices must be 1–3 "
                        f"distinct integers from 0 through {len(images)-1}; negative evidence_indices must be []. "
                        + ("For no event use has_violation=false, violation_type=null and no timecode_sec field."
                           if self.config.prompt_version == "frame_bound_v2"
                           else "For no event use has_violation=false, violation_type=null, timecode_sec=null."))
            audit["response"] = {"observed_action": response.observed_action,
                "has_violation": response.has_violation, "violation_type": response.violation_type,
                "timecode_sec": response.timecode_sec, "confidence": response.confidence,
                "evidence_indices": list(response.evidence_indices)}
            if not self.catalog:
                audit["outcome"] = "diagnostic_only"
                report.diagnostics.append(response.observed_action)
                return
            if not response.has_violation:
                audit["outcome"] = "no_supported_catalog_event"
                report.diagnostics.append("no_supported_catalog_event")
                return
            if response.confidence < self.config.confidence_threshold:
                audit["outcome"] = "below_confidence_threshold"
                report.diagnostics.append("below_confidence_threshold")
                return
            rule = self.policy[response.violation_type]
            stamps = [timestamps[i] for i in response.evidence_indices]
            if len(stamps) < rule["min_evidence"] or max(stamps) - min(stamps) < rule["min_context_sec"]:
                audit["outcome"] = "insufficient_temporal_evidence"
                report.diagnostics.append("insufficient_temporal_evidence")
                return
            # Allow at most 50 ms for decimal rounding of frame_index/FPS.
            if not timestamps[0] - .05 <= response.timecode_sec <= timestamps[-1] + .05:
                raise InferenceError("VLM timestamp lies outside the supplied evidence window")
            timestamp = min(timestamps[-1], max(timestamps[0], response.timecode_sec))
            evidence_indices = list(response.evidence_indices)
            if self.config.include_event_anchor:
                anchor = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - response.timecode_sec))
                if abs(timestamps[anchor] - response.timecode_sec) > .05:
                    raise InferenceError("VLM timestamp does not identify a supplied source frame")
                timestamp = timestamps[anchor]
                if anchor not in evidence_indices:
                    if len(evidence_indices) == 3:
                        audit["outcome"] = "event_anchor_not_supported"
                        report.diagnostics.append("event_anchor_not_supported")
                        return
                    # Original model proof already passed the unchanged policy.
                    # Include the additional frame the model selected as its time.
                    evidence_indices.insert(0, anchor)
            evidence = [EvidenceFrame(timestamps[i], images[i]) for i in evidence_indices]
            audit["returned_evidence_indices"], audit["finding_timecode_sec"] = evidence_indices, timestamp
            report.findings.append(Finding(response.violation_type, timestamp,
                response.observed_action, response.confidence, evidence))
            audit["outcome"] = "accepted_before_dedup"

        t = time.monotonic()
        with VideoReader(video_path) as reader:
            selector = CandidateSelector(self.config.window_frames, self.config.max_windows,
                min_gap_sec=max(3., reader.info.duration_sec / self.config.max_windows - self.config.context_sec / 2),
                coverage_sec=max(3., reader.info.duration_sec / self.config.max_windows - self.config.context_sec / 2),
                context_sec=self.config.context_sec, sample_fps=self.config.sample_fps,
                strategy=self.config.candidate_strategy)
            report.timings["extraction_sec"] += time.monotonic() - t
            iterator = iter(reader.sample(self.config.sample_fps, self.config.max_side,
                self.config.max_sample_frames, deadline))
            while True:
                check_deadline()
                t = time.monotonic()
                try:
                    frame = next(iterator)
                except StopIteration:
                    break
                report.timings["extraction_sec"] += time.monotonic() - t
                report.sampled_frames += 1
                t = time.monotonic()
                events, _ = extractor.update(frame)
                report.timings["detection_sec"] += time.monotonic() - t
                check_deadline()
                process(selector.update(frame, events))
            process(selector.finish())
        check_deadline()
        if not report.candidate_windows:
            raise VideoReadError("Video has too few sampled frames for a 4-frame temporal window")
        report.findings = deduplicate(report.findings, self.config.dedup_sec)
        for audit in report.window_audit:
            if audit["outcome"] == "accepted_before_dedup":
                response = audit["response"]
                audit["outcome"] = "accepted" if any(
                    f.violation_type == response["violation_type"] and f.timecode_sec == audit.get("finding_timecode_sec", response["timecode_sec"])
                    and f.confidence == response["confidence"] for f in report.findings) else "deduplicated"
        report.timings["total_sec"] = time.monotonic() - started
        return report


def _production_worker(connection, video_path, config, deadline=None):
    # Set before importing model libraries. Inputs are local files and in-memory bytes.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    # Keep library/kernel caches within the caller-authorized temporary root.
    cache_root = Path(config.temp_dir).resolve() / "runtime-cache"
    for variable, name in (("HF_HOME", "huggingface"), ("TORCH_HOME", "torch"),
                           ("CUDA_CACHE_PATH", "cuda"), ("TORCHINDUCTOR_CACHE_DIR", "inductor")):
        os.environ[variable] = str(cache_root / name)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    sys.dont_write_bytecode = True
    started = time.monotonic()
    try:
        from .network_guard import install_network_guard
        install_network_guard()
        for name in ("huggingface", "torch", "cuda", "inductor"):
            (cache_root / name).mkdir(parents=True, exist_ok=True)
        # Reject unreadable input before allocating model weights on the GPU.
        with VideoReader(video_path) as preflight:
            preflight.at(0, max_side=64)
        from .detector import LocalRTDetr
        from .vlm import LocalTransformersVLM
        import torch
        if config.device.startswith("cuda") and torch.cuda.is_available():
            # CPU preprocessing/dispatch still runs in a CUDA job. On the tested
            # Windows laptop the default 14-thread pool causes large latency
            # spikes; keep it local to this spawned ML worker, not the API host.
            if sys.platform == "win32":
                torch.set_num_threads(1)
            if config.memory_fraction is not None:
                torch.cuda.set_per_process_memory_fraction(config.memory_fraction)
            torch.cuda.reset_peak_memory_stats()
        load_started = time.monotonic()
        detector = LocalRTDetr(config.detector_dir, config.device)
        detector_loaded = time.monotonic()
        vlm = LocalTransformersVLM(config.vlm_dir, config.device, trace_dir=Path(config.temp_dir) / "diagnostics",
            release_cuda_cache=config.release_cuda_cache)
        log_dir = Path(config.temp_dir) / "diagnostics"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"runtime_{os.getpid()}.json").write_text(json.dumps({
            "pid": os.getpid(), "parent_pid": os.getppid(), "executable": sys.executable,
            "torch_version": torch.__version__, "cuda_version": torch.version.cuda,
            "cpu_threads": torch.get_num_threads(), "interop_threads": torch.get_num_interop_threads(),
            "detector_load_sec": detector_loaded - load_started,
            "vlm_load_sec": time.monotonic() - detector_loaded,
            "process_setup_and_model_load_sec": time.monotonic() - started,
            "analysis_remaining_sec": deadline - time.monotonic() if deadline is not None else None,
            "max_side": config.max_side, "sample_fps": config.sample_fps,
            "window_frames": config.window_frames, "max_windows": config.max_windows,
            "vlm_timeout_sec": config.vlm_timeout_sec,
        }), encoding="utf-8")
        report = Analyzer(detector, vlm, config, deadline=deadline).analyze(video_path)
        report.timings["model_load_sec"] = time.monotonic() - started - report.timings["total_sec"]
        if config.device.startswith("cuda"):
            report.peak_vram_bytes = torch.cuda.max_memory_allocated()
            report.peak_reserved_vram_bytes = torch.cuda.max_memory_reserved()
        message = (True, report)
    except Exception as exc:
        # The parent preserves known typed errors; no frames or descriptions in logs.
        message = _worker_error(exc)
    try:
        send_result(connection, message)
    finally:
        connection.close()


def _worker_error(exc):
    if isinstance(exc, MemoryError) or type(exc).__name__ == "OutOfMemoryError":
        return False, "InferenceError", f"Model memory exhausted: {exc}"
    return False, type(exc).__name__, str(exc)


def _supervise(target, args, timeout_sec):
    context = mp.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(send, *args))
    process.start()
    send.close()
    try:
        if not receive.poll(timeout_sec):
            raise AnalysisTimeoutError("Analysis process exceeded the wall time limit")
        try:
            message = receive.recv()
        except EOFError as exc:
            raise InferenceError("Analysis worker exited without a result") from exc
        if message[0]:
            return message[1]
        classes = {c.__name__: c for c in (VideoReadError, ModelNotConfiguredError, AnalysisTimeoutError, InferenceError)}
        raise classes.get(message[1], AnalyzerError)(message[2])
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=1.)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.)
        process.close()


def analyze_video_report(video_path: str, config: AnalyzerConfig | None = None) -> AnalysisReport:
    config = config or AnalyzerConfig.from_env()
    # Preserve the clear video-read error even on machines without model packages.
    if not Path(video_path).is_file():
        raise VideoReadError(f"Video does not exist: {video_path}")
    if (not config.detector_dir or not config.vlm_dir
            or not Path(config.detector_dir).is_dir() or not Path(config.vlm_dir).is_dir()):
        raise ModelNotConfiguredError("Set ANALYZER_DETECTOR_DIR and ANALYZER_VLM_DIR to approved local weights; no mock fallback")
    for directory in (config.detector_dir, config.vlm_dir):
        path = Path(directory)
        if not (path / "config.json").is_file() or not any(path.glob("*.safetensors")):
            raise ModelNotConfiguredError(f"Local model download is incomplete: {path}")
    load_catalog(config.catalog_path)
    started = time.monotonic()
    report = _supervise(_production_worker, (str(Path(video_path).resolve()), config, started + config.timeout_sec), config.timeout_sec)
    report.timings["wall_sec"] = time.monotonic() - started
    return report


def analyze_video(video_path: str) -> list[Finding]:
    return analyze_video_report(video_path).findings
