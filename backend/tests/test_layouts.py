"""Saved-layout API tests. Each test gets its own empty layouts file, so the
real data/layouts.json is never touched."""

import pytest
from fastapi.testclient import TestClient

from fs_telemetry import main
from fs_telemetry.layouts import BUILT_IN, LayoutStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "layouts", LayoutStore(tmp_path / "layouts.json"))
    return TestClient(main.app)


def test_built_ins_are_listed(client):
    names = [l["name"] for l in client.get("/api/layouts").json()]
    assert names == list(BUILT_IN)


def test_save_overwrite_and_delete(client):
    r = client.put("/api/layouts/My tyres", json={"panels": [["Tire Temp Core FL", "Tire Temp Core FR"], []]})
    assert r.status_code == 200
    assert r.json()["panels"] == [["Tire Temp Core FL", "Tire Temp Core FR"]]  # empty row dropped

    client.put("/api/layouts/My tyres", json={"panels": [["Ground Speed"]]})
    saved = [l for l in client.get("/api/layouts").json() if l["name"] == "My tyres"]
    assert saved == [{"name": "My tyres", "panels": [["Ground Speed"]], "built_in": False}]

    assert client.delete("/api/layouts/My tyres").status_code == 204
    assert client.delete("/api/layouts/My tyres").status_code == 404


def test_built_ins_are_protected(client):
    assert client.put("/api/layouts/Tyre temps", json={"panels": [["Gear"]]}).status_code == 403
    assert client.delete("/api/layouts/Tyre temps").status_code == 403


def test_bad_layouts_are_rejected(client):
    assert client.put("/api/layouts/x", json={"panels": []}).status_code == 422
    assert client.put("/api/layouts/x", json={"panels": [[f"c{i}" for i in range(9)]]}).status_code == 422
    assert client.put("/api/layouts/x", json={"nonsense": 1}).status_code == 422


def test_built_in_channels_exist_in_real_exports():
    """Catch typos in the built-in layouts against a real export, if one is here."""
    from fs_telemetry.config import TELEMETRY_DIR
    from fs_telemetry.ingest.motec_ld import read_ld

    files = list(TELEMETRY_DIR.rglob("*.ld")) if TELEMETRY_DIR.exists() else []
    if not files:
        pytest.skip("no .ld files on this machine")
    from fs_telemetry.maths import add_maths_channels

    # A telemetrick export: what CSP's logger logs depends on the car and its settings
    s = next((s for s in map(read_ld, sorted(files)) if "Lap Progression" not in s.channels), None)
    if s is None:
        pytest.skip("no telemetrick exports on this machine")
    channels = add_maths_channels(s).channels
    missing = {c for panels in BUILT_IN.values() for p in panels for c in p if c not in channels}
    assert not missing
