"""Read-only GPU check. No package or model downloads."""
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys


def inspect_environment():
    result = {"python": sys.version, "executable": sys.executable, "platform": platform.platform(), "packages": {}}
    for package in ("numpy", "opencv-python-headless", "openpyxl", "torch", "transformers"):
        try:
            result["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["packages"][package] = None
    if shutil.which("nvidia-smi"):
        try:
            proc = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10, check=True)
            result["nvidia_smi"] = proc.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            result["nvidia_smi_error"] = type(exc).__name__
    try:
        import torch
        result["torch_cuda"] = {"available": torch.cuda.is_available(), "build_cuda": torch.version.cuda}
        if torch.cuda.is_available():
            # Actual GPU allocation and kernel launch, rather than relying on nvidia-smi.
            x = torch.ones((16, 16), device="cuda")
            value = float((x @ x).sum())
            torch.cuda.synchronize()
            result["torch_cuda"].update(name=torch.cuda.get_device_name(0), kernel_check=value == 4096.,
                total_vram_bytes=torch.cuda.get_device_properties(0).total_memory)
    except (ImportError, OSError, RuntimeError) as exc:
        result["torch_cuda"] = {"available": None, "error": type(exc).__name__,
            "detail": "PyTorch missing or failed to initialize; GPU inference is not verified"}
    return result


if __name__ == "__main__":
    print(json.dumps(inspect_environment(), indent=2))
