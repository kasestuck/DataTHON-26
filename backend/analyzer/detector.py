"""Detector interface and optional local RT-DETR adapter. No cash/role classes."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .errors import InferenceError, ModelNotConfiguredError


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]  # normalized xyxy; independent of resolution


class Detector(Protocol):
    is_mock: bool

    def detect(self, bgr: np.ndarray) -> list[Detection]: ...


class LocalRTDetr:
    """PekingU/rtdetr_r18vd (Apache-2.0), provisioned separately under models/.

    COCO labels cover people, bags, phones, etc. They do not identify banknotes,
    cash-desk roles or violations. Neural inference needs separately installed
    torch/transformers and has not been validated until local weights are supplied.
    """
    is_mock = False

    def __init__(self, model_dir, device="cuda", threshold=.5):
        path = Path(model_dir).resolve()
        if not path.is_dir():
            raise ModelNotConfiguredError(f"Local detector directory missing: {path}")
        if not 0 <= threshold <= 1:
            raise ValueError("Invalid detector threshold")
        try:
            import torch
            from transformers import RTDetrForObjectDetection, RTDetrImageProcessor
        except ImportError as exc:
            raise ModelNotConfiguredError("Install optional torch/transformers dependencies") from exc
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise ModelNotConfiguredError("CUDA PyTorch is not available")
        self.torch, self.device, self.threshold = torch, device, threshold
        self.processor = RTDetrImageProcessor.from_pretrained(str(path), local_files_only=True)
        self.model = RTDetrForObjectDetection.from_pretrained(str(path), local_files_only=True,
            trust_remote_code=False, use_safetensors=True, disable_custom_kernels=True,
            use_pretrained_backbone=False).eval().to(device)

    def detect(self, bgr):
        h, w = bgr.shape[:2]
        try:
            inputs = self.processor(images=np.ascontiguousarray(bgr[:, :, ::-1]), return_tensors="pt").to(self.device)
            with self.torch.inference_mode():
                output = self.model(**inputs)
            result = self.processor.post_process_object_detection(output,
                target_sizes=self.torch.tensor([[h, w]], device=self.device), threshold=self.threshold)[0]
            detections = []
            for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
                coords = box.tolist()
                normalized = tuple(min(1., max(0., x / (w if i % 2 == 0 else h))) for i, x in enumerate(coords))
                if normalized[0] >= normalized[2] or normalized[1] >= normalized[3]:
                    continue  # Proposal lies entirely outside the visible image.
                detections.append(Detection(self.model.config.id2label[int(label)], float(score), normalized))
            return detections
        except self.torch.cuda.OutOfMemoryError as exc:
            self.torch.cuda.empty_cache()
            raise InferenceError("Detector CUDA out of memory; reduce resolution or unload VLM") from exc


class MockDetector:
    """Synthetic integration tests only. Never selected by analyze_video()."""
    is_mock = True

    def __init__(self, detections=()):
        self.detections = list(detections)

    def detect(self, bgr):
        return list(self.detections)
