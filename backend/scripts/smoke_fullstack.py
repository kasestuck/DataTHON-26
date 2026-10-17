"""Loopback site + headless browser tests, optionally with real local AI videos. No screenshots."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx


def run(positive: Path | None, negative: Path | None, report: Path):
    root = Path(__file__).resolve().parents[2]
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js and Playwright are needed for browser QA; see tests/frontend/README.md")
    private = root / "backend/analyzer/output"
    private.mkdir(parents=True, exist_ok=True)
    report = report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fullstack-", dir=private) as directory:
        data = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(API_KEY=secrets.token_urlsafe(32), DATA_DIR=str(data),
                   ANALYZER_TARGET="backend.analyzer:analyze_video", FRONTEND_DIST=str(root / "frontend"),
                   CATALOG_DIR=str(root / "backend/data"), ANALYZER_TEMP_DIR=str(data / "analyzer"),
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PUBLIC_ORIGIN="", CORS_ORIGINS="")
        with (data / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api.app:app_from_env",
                "--factory", "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--log-level", "warning"],
                cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                with httpx.Client(base_url=base, timeout=3, trust_env=False) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        if server.poll() is not None:
                            raise RuntimeError("Site exited before startup")
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise TimeoutError("Site startup timeout")
                        time.sleep(.2)
                browser_env = os.environ.copy()
                browser_env.update(FULLSTACK_URL=base, FULLSTACK_REPORT=str(report))
                for name in ("POSITIVE_VIDEO", "NEGATIVE_VIDEO", "CORRUPT_VIDEO"):
                    browser_env.pop(name, None)
                if positive:
                    browser_env["POSITIVE_VIDEO"] = str(positive.resolve())
                if negative:
                    browser_env["NEGATIVE_VIDEO"] = str(negative.resolve())
                if positive or negative:
                    corrupt = data / "corrupt.mov"
                    corrupt.write_bytes(b"\x00\x00\x00\x18ftypqt  " + bytes(12))
                    browser_env["CORRUPT_VIDEO"] = str(corrupt)
                browser_test = subprocess.run([node, str(root / "tests/frontend/browser.cjs")], cwd=root, env=browser_env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1100,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                output = browser_test.stdout.decode("utf-8", errors="replace")
                print(output.encode("ascii", errors="backslashreplace").decode("ascii"), flush=True)
                browser_test.check_returncode()
                with closing(sqlite3.connect(data / "jobs.sqlite3")) as db:
                    assert db.execute("SELECT COUNT(*) FROM jobs WHERE status='processing'").fetchone()[0] == 0
                assert not list((data / "uploads").glob("*.mov"))
                result = json.loads(report.read_text(encoding="utf-8"))
                result.update(transport="real loopback HTTP + Chrome DOM", uploads_removed=True)
                report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                return result
            finally:
                if os.name == "nt" and server.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    server.terminate()
                server.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positive", type=Path)
    parser.add_argument("--negative", type=Path)
    parser.add_argument("--output", type=Path, default=Path("backend/data/reports/integration_fullstack/browser.json"))
    args = parser.parse_args()
    run(args.positive, args.negative, args.output)


if __name__ == "__main__":
    main()
