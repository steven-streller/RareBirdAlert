import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_healthy(base_url: str, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server process exited early with code {proc.returncode}")
        try:
            urllib.request.urlopen(f"{base_url}/healthz", timeout=1)  # noqa: S310
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.2)
    raise RuntimeError(f"server did not become healthy within {timeout}s")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Runs the real app as a subprocess (real uvicorn, real HTTP, real
    SQLite file) so Playwright's browser has an actual URL to navigate to -
    unlike tests/conftest.py's `client` fixture (in-process ASGI, no browser
    involved). DISABLE_SCHEDULER keeps this from downloading the ~500k-row
    aircraft metadata DB or polling live flight data sources for a test that
    never watches an airport long enough to matter.
    """
    db_path = tmp_path_factory.mktemp("e2e") / "e2e.db"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = {
        "PATH": os.environ.get("PATH", ""),
        "RAREBIRDALERT_DB_PATH": str(db_path),
        "SESSION_SECRET_KEY": "e2e-test-session-secret",
        "DISABLE_SCHEDULER": "true",
        "REGISTRATION_ENABLED": "true",
    }
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
    )
    try:
        _wait_until_healthy(base_url, proc)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
