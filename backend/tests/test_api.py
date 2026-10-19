"""API tests against real exported telemetry. Skipped if no .ld files are on
this machine (set FS_TELEMETRY_DIR to point somewhere else)."""

import pytest
from fastapi.testclient import TestClient

from fs_telemetry.config import TELEMETRY_DIR
from fs_telemetry.main import app

pytestmark = pytest.mark.skipif(
    not any(TELEMETRY_DIR.rglob("*.ld")) if TELEMETRY_DIR.exists() else True,
    reason=f"no .ld files under {TELEMETRY_DIR}",
)

client = TestClient(app)


@pytest.fixture(scope="module")
def session():
    sessions = client.get("/api/sessions").json()
    assert sessions
    # pick one with a valid timed lap
    return next(s for s in sessions if s["best_lap_number"])


def test_every_file_is_listed_with_basic_info():
    sessions = client.get("/api/sessions").json()
    for s in sessions:
        assert s["driver"] and s["car"] and s["track"] and s["lap_count"] >= 1


def test_laps_match_best_lap(session):
    laps = client.get(f"/api/sessions/{session['id']}/laps").json()
    best = [l for l in laps if l["is_best"]]
    assert len(best) == 1
    assert best[0]["lap_time_s"] == session["best_lap_s"]


def test_lap_data_on_distance(session):
    r = client.get(
        f"/api/sessions/{session['id']}/laps/{session['best_lap_number']}/data",
        params={"channels": ["Ground Speed", "Gear"], "step_m": 5},
    ).json()
    d = r["data"]
    assert len(d["Distance"]) == len(d["Ground Speed"]) == len(d["Gear"])
    # A full lap should cover (nearly) the whole track
    assert d["Distance"][-1] > session["track_length_m"] - 20
    # ...and the time at the end of the grid should be (nearly) the lap time
    assert abs(d["Time"][-1] - session["best_lap_s"]) < 1.0


def test_every_timed_lap_resamples_to_a_full_lap():
    """Sweep all real exports: logger glitches must never cut a lap short."""
    from fs_telemetry.laps import lap_infos, resample_lap, track_length
    from fs_telemetry.main import catalog

    short = []
    for summary in catalog.summaries():
        s = catalog.get(summary.id)
        L = track_length(s)
        for info in lap_infos(s):
            if info.kind != "timed":
                continue
            d = resample_lap(s, info.number, [], step_m=5)["Distance"]
            if not len(d) or d[0] > 30 or d[-1] < L - 30:
                short.append(f"{summary.id} lap {info.number}")
    assert not short


def test_csv_download(session):
    r = client.get(f"/api/sessions/{session['id']}/laps/{session['best_lap_number']}/data.csv")
    assert r.headers["content-type"].startswith("text/csv")
    header = r.text.splitlines()[0]
    assert header == "Distance [m],Time [s],Ground Speed [kph],Throttle Pos [%],Brake Pos [%]"


def test_unknown_things_give_404(session):
    assert client.get("/api/sessions/nope/laps").status_code == 404
    assert client.get(f"/api/sessions/{session['id']}/laps/999/data").status_code == 404
    r = client.get(f"/api/sessions/{session['id']}/laps/2/data", params={"channels": ["Warp Drive"]})
    assert r.status_code == 404
