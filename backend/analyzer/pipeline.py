"""Synchronous integration pipeline. Production execution is process-supervised."""
from dataclasses import dataclass, field
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
from .policy import load_policy
from .video import VideoReader
from .vlm import build_prompt, generation_schema, validate_response


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
    def __init__(self, detector, vlm, config=None, *, allow_mock=False):
        self.config = config or AnalyzerConfig()
        self.detector, self.vlm = detector, vlm
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
        deadline = started + self.config.timeout_sec
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
            prompt = build_prompt(self.allowed_catalog, timestamps, self.policy)
            configure_schema = getattr(self.vlm, "set_response_schema", None)
            if configure_schema:
                configure_schema(generation_schema(self.allowed_catalog, timestamps))
            vlm_deadline = min(deadline, time.monotonic() + self.config.vlm_timeout_sec)
            for attempt in range(2):
                t = time.monotonic()
                raw = self.vlm.infer(images, prompt, vlm_deadline - t)
                report.timings["vlm_sec"] += time.monotonic() - t
                check_deadline()
                try:
                    response = validate_response(raw, self.allowed_catalog, len(images))
                    break
                except InferenceError:
                    # Private diagnostics remain under the authorized temporary root.
                    import json
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
                        "For no event use has_violation=false, violation_type=null, timecode_sec=null.")
            if not self.catalog:
                report.diagnostics.append(response.observed_action)
                return
            if not response.has_violation:
                report.diagnostics.append("no_supported_catalog_event")
                return
            if response.confidence < self.config.confidence_threshold:
                report.diagnostics.append("below_confidence_threshold")
                return
            rule = self.policy[response.violation_type]
            stamps = [timestamps[i] for i in response.evidence_indices]
            if len(stamps) < rule["min_evidence"] or max(stamps) - min(stamps) < rule["min_context_sec"]:
                report.diagnostics.append("insufficient_temporal_evidence")
                return
            # Allow at most 50 ms for decimal rounding of frame_index/FPS.
            if not timestamps[0] - .05 <= response.timecode_sec <= timestamps[-1] + .05:
                raise InferenceError("VLM timestamp lies outside the supplied evidence window")
            timestamp = min(timestamps[-1], max(timestamps[0], response.timecode_sec))
            evidence = [EvidenceFrame(timestamps[i], images[i]) for i in response.evidence_indices]
            report.findings.append(Finding(response.violation_type, timestamp,
                response.observed_action, response.confidence, evidence))

        t = time.monotonic()
        with VideoReader(video_path) as reader:
            selector = CandidateSelector(self.config.window_frames, self.config.max_windows,
                min_gap_sec=max(3., reader.info.duration_sec / self.config.max_windows - self.config.context_sec / 2),
                coverage_sec=max(3., reader.info.duration_sec / self.config.max_windows - self.config.context_sec / 2),
                context_sec=self.config.context_sec, sample_fps=self.config.sample_fps)
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
        report.timings["total_sec"] = time.monotonic() - started
        return report


def _production_worker(connection, video_path, config):
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
            if config.memory_fraction is not None:
                torch.cuda.set_per_process_memory_fraction(config.memory_fraction)
            torch.cuda.reset_peak_memory_stats()
        detector = LocalRTDetr(config.detector_dir, config.device)
        vlm = LocalTransformersVLM(config.vlm_dir, config.device, trace_dir=Path(config.temp_dir) / "diagnostics")
        report = Analyzer(detector, vlm, config).analyze(video_path)
        report.timings["model_load_sec"] = time.monotonic() - started - report.timings["total_sec"]
        if config.device.startswith("cuda"):
            report.peak_vram_bytes = torch.cuda.max_memory_allocated()
        connection.send((True, report))
    except Exception as exc:
        # The parent preserves known typed errors; no frames or descriptions in logs.
        connection.send(_worker_error(exc))
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
    report = _supervise(_production_worker, (str(Path(video_path).resolve()), config), config.timeout_sec)
    report.timings["wall_sec"] = time.monotonic() - started
    return report


def analyze_video(video_path: str) -> list[Finding]:
    return analyze_video_report(video_path).findings
