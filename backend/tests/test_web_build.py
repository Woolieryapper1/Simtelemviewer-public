"""The built web pages (backend/fs_telemetry/web) are shipped in the package, so
they must be rebuilt whenever web/ changes. These tests fail if someone forgot:
run `npm --prefix web run build` and commit the result."""

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from fs_telemetry.main import WEB_DIR, app

ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = ROOT / "web"
# Must match SOURCES in web/vite.config.js
SOURCES = ["index.html", "package-lock.json", "vite.config.js", "src", "public"]


def source_hash() -> str:
    """Same fingerprint as the build writes: SHA-256 over each file's path and contents."""
    files = []
    for s in SOURCES:
        p = WEB_SRC / s
        files += sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
    h = hashlib.sha256()
    for f in sorted(files, key=lambda f: f.relative_to(WEB_SRC).as_posix()):
        h.update(f.relative_to(WEB_SRC).as_posix().encode() + b"\n")
        h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\n")  # line endings don't count
    return h.hexdigest()


def test_built_pages_match_sources():
    stamp = WEB_DIR / "build-stamp.txt"
    assert stamp.exists(), "web pages not built: run `npm --prefix web run build`"
    assert stamp.read_text().strip() == source_hash(), (
        "web/ changed since the last build: run `npm --prefix web run build` and commit backend/fs_telemetry/web")


def test_dashboard_is_served():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200 and '<div id="root">' in r.text
    assert client.get("/vendor/plotly.min.js").status_code == 200


def test_pages_are_never_reused_stale():
    # The browser must check for a newer page each time, or it can keep showing the old app after an update
    r = TestClient(app).get("/")
    assert r.headers["cache-control"] == "no-cache"
