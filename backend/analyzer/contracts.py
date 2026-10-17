"""In-process integration contract. JPEG bytes are never serialized implicitly."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class EvidenceFrame:
    timestamp_sec: float
    jpeg_bytes: bytes

    def __post_init__(self):
        if not math.isfinite(self.timestamp_sec) or self.timestamp_sec < 0:
            raise ValueError("Invalid evidence timestamp")
        if not isinstance(self.jpeg_bytes, bytes) or not self.jpeg_bytes:
            raise ValueError("Evidence must contain JPEG bytes")


@dataclass(frozen=True)
class Finding:
    violation_type: str
    timecode_sec: float
    description: str
    confidence: float
    evidence_frames: list[EvidenceFrame]

    def __post_init__(self):
        if not self.violation_type or self.violation_type == "unknown":
            raise ValueError("A finding requires a confirmed violation type")
        if not self.description.strip():
            raise ValueError("A finding requires an observable description")
        if not math.isfinite(self.timecode_sec) or self.timecode_sec < 0:
            raise ValueError("Invalid finding timestamp")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Confidence must be in [0, 1]")
        if not 1 <= len(self.evidence_frames) <= 3:
            raise ValueError("A finding requires 1–3 evidence frames")
