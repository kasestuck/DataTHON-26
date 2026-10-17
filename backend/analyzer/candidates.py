from collections import deque
from dataclasses import dataclass

from .video import Frame


@dataclass(frozen=True)
class Candidate:
    frames: tuple[Frame, ...]
    reasons: tuple[str, ...]
    trigger_timestamp_sec: float | None = None


class CandidateSelector:
    """Bounded 4–8 frame windows with past/future context and periodic coverage.

    Scene changes and people motion are only selection signals. Periodic windows
    reduce blind spots for subtle actions; recall across camera views is unmeasured.
    """
    def __init__(self, window_frames=6, max_windows=12, min_gap_sec=3., coverage_sec=12., context_sec=0., sample_fps=2., strategy="uniform"):
        if not 4 <= window_frames <= 8 or max_windows < 1 or min_gap_sec < 0 or coverage_sec <= 0:
            raise ValueError("Invalid candidate window budget")
        if context_sec < 0 or sample_fps <= 0:
            raise ValueError("Invalid temporal context")
        if strategy not in {"uniform", "trigger"}:
            raise ValueError("Unknown candidate strategy")
        self.strategy, self.trigger_frame_index, self.trigger_timestamp = strategy, None, None
        self.window_frames, self.context_sec = window_frames, context_sec
        self.buffer = deque(maxlen=max(window_frames, int(context_sec * sample_fps) + 1))
        self.max_windows, self.min_gap, self.coverage = max_windows, min_gap_sec, coverage_sec
        self.last_selected, self.count = float("-inf"), 0
        self.pending, self.after = set(), 0
        self.target_time = None

    def update(self, frame, events):
        self.buffer.append(frame)
        if self.count >= self.max_windows:
            return None
        if self.pending:
            self.after -= 1
        elif frame.timestamp_sec - self.last_selected >= self.min_gap:
            reasons = {event.type for event in events}
            if frame.timestamp_sec - self.last_selected >= self.coverage:
                reasons.add("periodic_coverage")
            if reasons:
                self.pending, self.after = reasons, self.window_frames // 2
                self.trigger_frame_index, self.trigger_timestamp = frame.frame_index, frame.timestamp_sec
                self.target_time = frame.timestamp_sec + self.context_sec / 2
        ready = frame.timestamp_sec >= self.target_time if self.context_sec and self.target_time is not None else self.after <= 0
        if self.pending and ready and len(self.buffer) >= 4:
            return self._emit()
        return None

    def _emit(self):
        frames = list(self.buffer)
        if len(frames) > self.window_frames:
            indices = [round(i * (len(frames) - 1) / (self.window_frames - 1)) for i in range(self.window_frames)]
            if self.strategy == "trigger":
                center = next((i for i, frame in enumerate(frames) if frame.frame_index == self.trigger_frame_index), None)
                if center is not None and center not in indices:
                    # Preserve the full before/after range and the actual trigger;
                    # replace one interior sample, without adding images or calls.
                    replace_index = max(indices[1:-1], key=lambda i:
                        (abs(frames[i].timestamp_sec - self.trigger_timestamp), frames[i].timestamp_sec))
                    indices = sorted((set(indices) - {replace_index}) | {center})
            frames = [frames[i] for i in indices]
        candidate = Candidate(tuple(frames), tuple(sorted(self.pending)), self.trigger_timestamp)
        self.last_selected, self.count = self.buffer[-1].timestamp_sec, self.count + 1
        self.pending = set()
        return candidate

    def finish(self):
        if self.pending and len(self.buffer) >= 4 and self.count < self.max_windows:
            return self._emit()
        return None
