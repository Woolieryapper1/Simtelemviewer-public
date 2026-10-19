"""Setup library tests. Each test gets an empty library and a fake AC setups
folder, so the real data/setups and Documents/Assetto Corsa are never touched."""

import pytest
from fastapi.testclient import TestClient

from fs_telemetry import main
from fs_telemetry.setups import SetupLibrary, parse_setup_ini, pretty

BASE = """[PRESSURE_LF]
VALUE=12

[CAMBER_LF]
VALUE=-16

[ARB_FRONT]
VALUE=1

[FRONT_BIAS]
VALUE=66

[CAR]
MODEL=madformulateam_mft02_hy
"""


@pytest.fixture
def ac_dir(tmp_path):
    d = tmp_path / "ac_setups"
    (d / "madformulateam_mft02_hy" / "generic").mkdir(parents=True)
    (d / "madformulateam_mft02_hy" / "generic" / "base.ini").write_text(BASE)
    (d / "madformulateam_mft02_hy" / "generic" / "softer.ini").write_text(BASE.replace("[ARB_FRONT]\nVALUE=1", "[ARB_FRONT]\nVALUE=0"))
    (tmp_path / "secret.ini").write_text(BASE)  # outside the setups folder
    return d


@pytest.fixture
def client(tmp_path, ac_dir, monkeypatch):
    monkeypatch.setattr(main, "setup_library", SetupLibrary(tmp_path / "library.json", ac_dir, tmp_path / "no_iracing"))
    return TestClient(main.app)


def test_parse_setup_ini():
    car, values = parse_setup_ini(BASE)
    assert car == "madformulateam_mft02_hy"
    assert values == {"PRESSURE_LF": 12, "CAMBER_LF": -16, "ARB_FRONT": 1, "FRONT_BIAS": 66}
    assert pretty("DAMP_FAST_BUMP_LR") == "Damp fast bump RL"


def test_import_from_ac_folder_and_compare(client):
    avail = client.get("/api/setups/available").json()
    assert [a["name"] for a in avail] == ["base", "softer"] and not any(a["in_library"] for a in avail)
    ids = [client.post("/api/setups/import", json={"path": a["path"]}).json()["id"] for a in avail]
    assert all(a["in_library"] for a in client.get("/api/setups/available").json())

    c = client.get("/api/setups/compare", params={"id": ids}).json()
    assert c["changed_count"] == 1
    changed = [r for r in c["rows"] if r["changed"]]
    assert changed == [{"field": "ARB_FRONT", "label": "Arb front", "group": "Anti-roll bars", "values": [1, 0], "changed": True}]
    assert [r["group"] for r in c["rows"]] == ["Tyres", "Alignment", "Anti-roll bars", "Brakes"]


def test_versions_notes_and_delete(client):
    up = lambda text: client.post("/api/setups", json={"name": "Race", "ini_text": text, "notes": "first try"}).json()
    v1 = up(BASE)
    assert v1["version"] == 1 and v1["car"] == "madformulateam_mft02_hy" and v1["track"] == "generic"
    assert up(BASE)["id"] == v1["id"]  # identical re-upload: no new version
    v2 = up(BASE.replace("VALUE=66", "VALUE=64"))
    assert v2["version"] == 2
    assert client.patch(f"/api/setups/{v1['id']}", json={"notes": "baseline"}).json()["notes"] == "baseline"
    assert client.delete(f"/api/setups/{v1['id']}").status_code == 204
    assert [s["version"] for s in client.get("/api/setups").json()] == [2]


def test_bad_input_is_refused(client):
    assert client.post("/api/setups/import", json={"path": "../secret.ini"}).status_code == 422
    assert client.post("/api/setups", json={"name": "x", "ini_text": "not an ini"}).status_code == 422
    assert client.post("/api/setups", json={"name": "x", "ini_text": "[A]\nVALUE=1"}).status_code == 422  # no car
    assert client.get("/api/setups/compare", params={"id": ["nope"]}).status_code == 422
