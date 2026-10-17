"""Explicit provisioning only. Never imported by inference or given private inputs.

The user approved these two repositories and their sizes in this chat. Running
this command on another machine is an explicit request to download ~4.35 GB.
"""
import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote

REPOSITORIES = {
    "detector": ("PekingU/rtdetr_r18vd", "rtdetr_r18vd"),
    "vlm": ("Qwen/Qwen3-VL-2B-Instruct", "qwen3_vl_2b"),
}
PATTERNS = ["*.json", "*.txt", "*.safetensors", "*.model", "LICENSE*", "README.md"]


def curl_snapshot(repository, info, local_dir, runner=subprocess.run):
    """Resumable Windows fallback; verify LFS SHA256 or Git blob SHA1."""
    local_dir = Path(local_dir).resolve()
    local_dir.mkdir(parents=True, exist_ok=True)
    for item in info.siblings:
        name = item.rfilename
        if not any(fnmatch.fnmatch(name, pattern) for pattern in PATTERNS):
            continue
        destination = (local_dir / name).resolve()
        if not destination.is_relative_to(local_dir):
            raise ValueError("Unsafe model filename")
        destination.parent.mkdir(parents=True, exist_ok=True)
        lfs = getattr(item, "lfs", None)
        expected_sha = (lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)) if lfs else None

        def valid(path):
            if not path.is_file() or path.stat().st_size != item.size:
                return False
            with path.open("rb") as stream:
                digest = hashlib.sha256() if expected_sha else hashlib.sha1(f"blob {item.size}\0".encode())
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                return digest.hexdigest() == (expected_sha or item.blob_id)

        if valid(destination):
            continue
        partial = destination.with_suffix(destination.suffix + ".part")
        url = f"https://huggingface.co/{repository}/resolve/{info.sha}/{quote(name, safe='/')}"
        print(json.dumps({"file": name, "expected_bytes": item.size}), flush=True)
        runner(["curl.exe", "--silent", "--show-error", "--fail", "--location", "--continue-at", "-",
            "--retry", "2", "--connect-timeout", "15", "--speed-time", "60", "--speed-limit", "1024",
            "--max-time", "3600", "--output", str(partial), url], check=True)
        if not valid(partial):
            raise ValueError(f"Model file hash or size mismatch: {name}")
        partial.replace(destination)


def download_models(destination, selected, api=None, downloader=None, transport="hub"):
    if api is None or downloader is None:
        from huggingface_hub import HfApi, snapshot_download
        api, downloader = HfApi(), snapshot_download
    records = []
    for key in selected:
        repository, name = REPOSITORIES[key]
        info = api.model_info(repository, files_metadata=True)
        card = info.card_data.to_dict() if info.card_data is not None else {}
        if card.get("license") != "apache-2.0":
            raise ValueError(f"Unexpected license for {repository}; download stopped")
        size = sum(item.size or 0 for item in info.siblings)
        print(json.dumps({"repository": repository, "revision": info.sha, "repository_bytes": size,
                          "license": card["license"]}), flush=True)
        local_dir = Path(destination).resolve() / name
        if transport == "curl":
            curl_snapshot(repository, info, local_dir)
        elif transport == "hub":
            downloader(repository, revision=info.sha, local_dir=str(local_dir), allow_patterns=PATTERNS)
        else:
            raise ValueError("Unsupported download transport")
        record = {"repository": repository, "revision": info.sha, "repository_bytes": size,
                  "license": card["license"], "local_dir": str(local_dir)}
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "provenance.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser(description="Explicit download of approved model weights; no dataset inputs")
    parser.add_argument("--models", choices=["both", "detector", "vlm"], default="both")
    parser.add_argument("--destination", default="models")
    parser.add_argument("--transport", choices=["hub", "curl"], default="hub")
    args = parser.parse_args()
    # Keep provisioning caches under the ignored model root, not the user's home.
    os.environ["HF_HOME"] = str(Path(args.destination).resolve() / ".cache")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import torch
    if not torch.cuda.is_available():
        parser.error("CUDA must be verified before provisioning weights on this machine")
    selected = list(REPOSITORIES) if args.models == "both" else [args.models]
    download_models(args.destination, selected, transport=args.transport)


if __name__ == "__main__":
    main()
