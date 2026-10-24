"""Tests for the CSV reader. The files are made here, small and with known
answers: a car going round a circular track, written out the ways different
tools write CSV (commas or semicolons, decimal commas, units rows, uneven
rows, missing columns)."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fs_telemetry.ingest.csv_import import CsvError, MappingStore, detect_layout, read_csv, source_signature
from fs_telemetry.laps import lap_infos, resample_lap, track_length
from fs_telemetry.maths import add_maths_channels

R = 150.0  # m - the track is a circle
L = 2 * np.pi * R  # ~942 m
V = 40.0  # m/s
START = 0.9 * L  # the car sets off 10% of a lap before the line
LAT0, LON0 = 50.0, -1.0
EARTH_RADIUS_M = 6_371_000.0


def run(freq=20.0, laps=3.5, uneven=False):
    """A run at constant speed: time, lap distance, lap number, GPS, pedals, steering."""
    n = int((laps * L - START) / V * freq)  # ends part-way round the last lap
    t = np.arange(n) / freq
    if uneven:  # rows 20-80 ms apart, as a logger with a busy CPU writes them
        t = np.cumsum(np.random.default_rng(1).uniform(0.02, 0.08, n))
        t -= t[0]
    s = START + V * t  # distance from the line at the start of lap 1
    lap = np.floor(s / L).astype(int)  # 0 = out lap
    theta = s / L * 2 * np.pi
    x, y = R * np.sin(theta), -R * np.cos(theta)
    return {
        "t": t, "lapdist": s % L, "lap": lap, "s": s,
        "lat": LAT0 + np.degrees(y / EARTH_RADIUS_M),
        "lon": LON0 + np.degrees(x / (EARTH_RADIUS_M * np.cos(np.radians(LAT0)))),
        "throttle": 0.5 + 0.4 * np.sin(theta), "brake": np.clip(-np.sin(theta), 0, 1) * 0.8,
        "steer": 30 * np.cos(theta),
    }


def write(path, header, rows, sep=",", decimal=".", above="", units=None):
    lines = [above] if above else []
    lines.append(sep.join(header))
    if units:
        lines.append(sep.join(units))
    for r in rows:
        lines.append(sep.join(f"{v}".replace(".", decimal) if v != "" else "" for v in r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def standard_csv(path, **kw):
    r = run(**kw)
    rows = zip(*(np.round(r[k], 7) for k in ("t", "lap", "lat", "lon")),
               np.round(r["throttle"] * 100, 2), np.round(r["brake"] * 100, 2), np.round(r["steer"], 2),
               np.full(len(r["t"]), V * 3.6))
    return write(path, ["Time", "Lap", "Latitude", "Longitude", "Throttle", "Brake", "Steering", "Speed"], rows), r


@pytest.fixture
def store(tmp_path):
    return MappingStore(tmp_path / "mappings.json")


def test_plain_csv_with_laps_gps_and_driver_inputs(tmp_path, store):
    path, r = standard_csv(tmp_path / "plain.csv")
    s = add_maths_channels(read_csv(path, store))
    assert s.source == "csv"
    for name in ("Ground Speed", "Throttle Pos", "Brake Pos", "Steering Angle", "Car Coord X", "Car Coord Y", "Lap Distance"):
        assert name in s.channels
    assert s.channels["Ground Speed"].unit == "kph"
    infos = lap_infos(s)
    assert [l.kind for l in infos] == ["out", "timed", "timed", "incomplete"]
    assert infos[1].lap_time == pytest.approx(L / V, abs=0.06)  # to a row (20 Hz)
    assert track_length(s) == pytest.approx(L, abs=3)
    # The map: a circle of radius R
    x, y = s.channels["Car Coord X"].data, s.channels["Car Coord Y"].data
    centre = (x.max() + x.min()) / 2, (y.max() + y.min()) / 2
    assert np.hypot(x - centre[0], y - centre[1]) == pytest.approx(R, abs=1)
    # Resampled on distance like any other run
    lap = resample_lap(s, 2, ["Throttle Pos"], step_m=5)
    assert lap["Distance"][-1] > L - 10
    assert "Throttle Brake Overlap" in s.channels  # maths channels work


def test_semicolons_decimal_commas_and_a_units_row(tmp_path, store):
    r = run()
    rows = zip(np.round(r["t"], 3), r["lap"], np.round(r["throttle"], 4), np.round(r["brake"], 4),
               np.round(np.radians(r["steer"]), 5), np.full(len(r["t"]), V))
    path = write(tmp_path / "excel.csv", ["Zeit", "Lap", "Throttle", "Brake", "Steering", "Speed"], rows,
                 sep=";", decimal=",", units=["s", "", "", "", "rad", "m/s"])
    layout = detect_layout(path.read_text())
    assert (layout.delimiter, layout.decimal_comma) == (";", True)
    assert layout.units[-1] == "m/s"
    # "Zeit" isn't a name it knows: it has to be told which column is the time
    with pytest.raises(CsvError, match="no time column"):
        read_csv(path, store)
    saved = json.loads(store.path.read_text())
    entry = saved["sources"][source_signature(layout.names, layout.units)]
    entry["columns"]["Zeit"]["channel"] = "Time"
    store.path.write_text(json.dumps(saved))
    c = read_csv(path, store).channels
    assert c["Ground Speed"].data[100] == pytest.approx(V * 3.6)  # m/s -> kph
    assert c["Throttle Pos"].data.max() > 80  # 0..1 -> %
    assert np.abs(c["Steering Angle"].data).max() == pytest.approx(30, abs=0.1)  # rad -> deg


def test_uneven_rows_and_a_slower_gps_go_onto_one_even_clock(tmp_path, store):
    r = run(uneven=True)
    rows = []
    for i in range(len(r["t"])):
        gps = i % 5 == 0  # GPS only every 5th row; blank cells between
        rows.append([round(r["t"][i], 4), r["lap"][i], round(r["lat"][i], 7) if gps else "",
                     round(r["lon"][i], 7) if gps else "", V * 3.6])
    path = write(tmp_path / "uneven.csv", ["Time [s]", "Lap", "GPS Lat", "GPS Lon", "Speed [km/h]"], rows)
    s = read_csv(path, store)
    c = s.channels
    freq = c["Ground Speed"].freq
    assert freq == 20  # median row spacing 50 ms
    n = len(c["Ground Speed"].data)
    assert n == pytest.approx(r["t"][-1] * freq, abs=2)
    assert all(len(ch.data) == n for ch in c.values())
    # positions are interpolated between the GPS rows, at the right times
    i = 1000
    theta = (START + V * i / freq) / L * 2 * np.pi
    x = c["Car Coord X"].data
    assert x[i] - (x.max() + x.min()) / 2 == pytest.approx(R * np.sin(theta), abs=2)
    assert [l.kind for l in lap_infos(s)] == ["out", "timed", "timed", "incomplete"]


def test_laps_from_gps_when_there_is_no_lap_column(tmp_path, store):
    r = run()
    rows = zip(np.round(r["t"], 3), np.round(r["lat"], 7), np.round(r["lon"], 7), np.full(len(r["t"]), V * 3.6))
    s = read_csv(write(tmp_path / "nolap.csv", ["time", "lat", "lon", "speed"], rows), store)
    infos = lap_infos(s)
    # Where the car set off is used as the line: it comes back past it twice in 2.6 laps
    assert [l.kind for l in infos] == ["out", "timed", "incomplete"]
    assert infos[1].lap_time == pytest.approx(L / V, abs=0.06)
    assert "GPS" in s.details["CSV Laps From"]


def test_laps_from_a_lap_distance_going_back_to_zero(tmp_path, store):
    r = run()
    rows = zip(np.round(r["t"], 3), np.round(r["lapdist"], 2), np.full(len(r["t"]), V * 3.6))
    s = read_csv(write(tmp_path / "dist.csv", ["Time", "Lap Distance", "Speed"], rows), store)
    infos = lap_infos(s)
    assert [l.kind for l in infos] == ["out", "timed", "timed", "incomplete"]
    assert infos[1].lap_time == pytest.approx(L / V, abs=0.06)
    assert s.beacons[0] == pytest.approx((L - START) / V, abs=0.06)


def test_laps_from_a_lap_timer_as_the_only_clock(tmp_path, store):
    r = run()
    lap_start = np.where(r["lap"] == 0, -START / V, (r["lap"] * L - START) / V)
    rows = zip(np.round(r["t"] - lap_start, 3), np.full(len(r["t"]), V * 3.6))
    s = read_csv(write(tmp_path / "laptimer.csv", ["Lap Time", "Speed"], rows), store)
    # The timer is joined up into one clock, and each time it goes back to 0 is a lap
    infos = lap_infos(s)
    assert [l.kind for l in infos] == ["out", "timed", "timed", "incomplete"]
    assert s.beacons[0] == pytest.approx((L - START) / V, abs=0.06)
    assert infos[1].lap_time == pytest.approx(L / V, abs=0.06)
    assert "going back to 0" in s.details["CSV Laps From"]


def test_no_lap_information_is_one_run(tmp_path, store):
    r = run()
    rows = zip(np.round(r["t"], 3), np.full(len(r["t"]), V * 3.6), np.round(r["throttle"] * 100, 1))
    s = read_csv(write(tmp_path / "onerun.csv", ["Time", "Speed", "Throttle"], rows), store)
    assert len(s.laps) == 1
    assert track_length(s) == pytest.approx(V * r["t"][-1], rel=0.01)  # distance from the speed


def test_distance_spaced_rows_without_a_time_column(tmp_path, store):
    r = run(laps=1.5)
    d = np.arange(0, 600, 1.0)  # a row every metre, as the app's own CSV download
    rows = zip(d, np.full(len(d), V * 3.6), np.round(np.interp(d, r["s"] - START, r["throttle"]) * 100, 2))
    s = read_csv(write(tmp_path / "bydist.csv", ["Distance [m]", "Ground Speed [kph]", "Throttle Pos [%]"], rows), store)
    assert s.duration == pytest.approx(599 / V, abs=0.1)
    assert "distance" in s.details["CSV Rows Spaced By"]


def test_unclear_files_say_why(tmp_path, store):
    path = write(tmp_path / "what.csv", ["A", "B", "C"], [[1, 2, 3], [2, 3, 4], [3, 4, 5]])
    with pytest.raises(CsvError, match="Can't tell how the rows are spaced.*A, B, C"):
        read_csv(path, store)
    (tmp_path / "empty.csv").write_text("hello\n")
    with pytest.raises(CsvError):
        read_csv(tmp_path / "empty.csv", store)
    backwards = write(tmp_path / "back.csv", ["Time", "Speed"], [[t, 100] for t in np.random.default_rng(0).permutation(50)])
    with pytest.raises(CsvError, match="jumps"):
        read_csv(backwards, store)


def test_mapping_is_remembered_per_source(tmp_path, store):
    path, _ = standard_csv(tmp_path / "a.csv")
    read_csv(path, store)
    saved = json.loads(store.path.read_text())
    (entry,) = saved["sources"].values()
    assert entry["columns"]["Speed"] == {"channel": "Ground Speed", "unit": ""}
    assert entry["columns"]["Latitude"]["channel"] == "GPS Latitude"
    # Someone fixes the mapping by hand: the speed is really in mph, and names the track
    entry["columns"]["Speed"]["unit"] = "mph"
    entry["track"] = "Test Circle"
    store.path.write_text(json.dumps(saved))
    # The next file from the same source (same columns) uses it
    path2, _ = standard_csv(tmp_path / "b.csv", laps=2.5)
    s = read_csv(path2, store)
    assert s.channels["Ground Speed"].data[50] == pytest.approx(V * 3.6 * 1.609344, rel=1e-4)
    assert s.track == "Test Circle"
    # A different source gets its own entry; the first is left alone
    r = run()
    write(tmp_path / "other.csv", ["Time", "Speed", "Gear"], zip(r["t"], np.full(len(r["t"]), 100), np.full(len(r["t"]), 3)))
    read_csv(tmp_path / "other.csv", store)
    saved = json.loads(store.path.read_text())
    assert len(saved["sources"]) == 2
    assert saved["sources"][source_signature(["Time", "Lap", "Latitude", "Longitude", "Throttle", "Brake",
                                              "Steering", "Speed"])]["track"] == "Test Circle"


def test_a_column_missing_from_the_saved_mapping_keeps_the_unit_from_the_file(tmp_path, store):
    r = run(laps=1.5)
    rows = list(zip(np.round(r["t"], 3), np.round(r["s"] / 10, 2), np.round(r["throttle"] * 100, 1)))
    path = write(tmp_path / "a.csv", ["Time", "Speed", "Throttle"], rows, units=["s", "m/s", "%"])
    read_csv(path, store)
    saved = json.loads(store.path.read_text())
    (entry,) = saved["sources"].values()
    del entry["columns"]["Speed"]  # someone deleted a line while editing
    store.path.write_text(json.dumps(saved))
    s = read_csv(path, store)
    assert s.channels["Ground Speed"].unit == "kph"  # guessed again, with the file's m/s unit converted
    assert s.channels["Ground Speed"].data[10] == pytest.approx(r["s"][10] / 10 * 3.6, rel=1e-3)
    assert "Throttle Pos" in s.channels


def test_a_broken_mapping_file_is_never_overwritten(tmp_path, store):
    store.path.write_text("{ oops")
    path, _ = standard_csv(tmp_path / "a.csv")
    assert "Ground Speed" in read_csv(path, store).channels  # guesses used instead
    assert store.path.read_text() == "{ oops"


def test_motec_style_export_with_details_and_beacons_above_the_header(tmp_path, store):
    r = run()
    beacons = [(k * L - START) / V for k in (1, 2, 3)]
    above = "\n".join([
        '"Format","MoTeC CSV File",,,"Workbook","x"',
        '"Venue","Test Circle",,,"Worksheet","y"',
        '"Vehicle","MFT02"',
        '"Driver","Jo Bloggs"',
        '"Log Date","18/10/2026",,,"Origin Time","0","s"',
        '"Log Time","14:05:00"',
        f'"Beacon Markers","{" ".join(f"{b:.3f}" for b in beacons)}"',
        "", "",
    ])
    rows = zip(np.round(r["t"], 3), np.full(len(r["t"]), V * 3.6), np.round(r["lat"], 7), np.round(r["lon"], 7))
    path = write(tmp_path / "motec.csv", ['"Time"', '"Ground Speed"', '"GPS Latitude"', '"GPS Longitude"'], rows,
                 above=above, units=['"s"', '"km/h"', '"deg"', '"deg"'])
    s = read_csv(path, store)
    assert (s.driver, s.car, s.track, s.date, s.time) == ("Jo Bloggs", "MFT02", "Test Circle", "18/10/2026", "14:05:00")
    assert s.beacons == pytest.approx(beacons, abs=1e-3)  # written to the ms
    assert "beacon markers" in s.details["CSV Laps From"]


# --- Through the API: the Telemetry and Track pages, and a round trip ------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fs_telemetry import main, sectors
    from fs_telemetry.catalog import Catalog

    folder = tmp_path / "csv"
    folder.mkdir()
    monkeypatch.setattr(main, "catalog", Catalog(root=tmp_path / "no-ld", iracing_root=None, csv_root=folder, csp_root=None))
    monkeypatch.setattr(sectors, "_cache", {})
    return TestClient(main.app), folder


def test_csv_opens_in_the_telemetry_and_track_pages(api):
    client, folder = api
    standard_csv(folder / "circle run.csv")
    (summary,) = client.get("/api/sessions").json()
    assert summary["id"] == "circle run" and summary["source"] == "csv"
    assert summary["lap_count"] == 4 and summary["best_lap_s"] == pytest.approx(L / V, abs=0.06)
    names = {c["name"] for c in client.get("/api/sessions/circle run/channels").json()}
    assert {"Ground Speed", "Throttle Pos", "Brake Pos", "Steering Angle", "Car Coord X"} <= names
    laps = client.get("/api/sessions/circle run/laps").json()
    data = client.get("/api/sessions/circle run/laps/2/data",
                      params={"channels": ["Ground Speed", "Steering Angle"], "step_m": 5}).json()["data"]
    assert data["Distance"][-1] > L - 10 and data["Ground Speed"][0] == pytest.approx(V * 3.6, abs=0.1)
    timed = [l["number"] for l in laps if l["kind"] == "timed"]
    c = client.get("/api/compare", params={"lap": [f"circle run:{n}" for n in timed]})
    assert c.status_code == 200
    body = c.json()
    assert len(body["map"]["x"]) > 100 and len(body["lines"]) == 2  # the track line
    assert body["lines"][0]["throttle"] is not None
    assert client.get("/api/sessions/circle run/stint").status_code == 200


def test_csv_without_gps_says_so_on_the_track_page(api):
    client, folder = api
    r = run()
    write(folder / "nogps.csv", ["Time", "Lap", "Speed"], zip(np.round(r["t"], 3), r["lap"], np.full(len(r["t"]), 144)))
    c = client.get("/api/compare", params={"lap": ["nogps:2"]})
    assert c.status_code == 422 and "GPS" in c.json()["detail"]


def test_round_trip_through_the_apps_own_csv_download(api):
    client, folder = api
    standard_csv(folder / "original.csv")
    channels = ["Ground Speed", "Throttle Pos", "Brake Pos", "Steering Angle"]
    r = client.get("/api/sessions/original/laps/2/data.csv", params={"channels": channels, "step_m": 1})
    (folder / "exported.csv").write_text(r.text)
    names = {c["name"] for c in client.get("/api/sessions/exported/channels").json()}
    assert set(channels) <= names
    before = client.get("/api/sessions/original/laps/2/data", params={"channels": channels, "step_m": 5}).json()["data"]
    after = client.get("/api/sessions/exported/laps/1/data", params={"channels": channels, "step_m": 5}).json()["data"]
    k = min(len(before["Distance"]), len(after["Distance"])) - 2
    assert k > 150
    assert after["Distance"][:k] == pytest.approx(before["Distance"][:k], abs=0.01)
    for name in channels:
        assert after[name][:k] == pytest.approx(before[name][:k], abs=1.0)
    assert after["Time"][k] == pytest.approx(before["Time"][k], abs=0.05)


def test_same_column_names_with_different_units_do_not_share_a_mapping(tmp_path, store):
    header = ["Time", "Speed"]
    for unit, expected in (("m/s", 36.0), ("km/h", 10.0), ("mph", 16.09344)):
        path = write(tmp_path / f"speed-{len(unit)}-{unit.replace('/', '-')}.csv", header,
                     [[0, 10], [1, 10], [2, 10]], units=["s", unit])
        session = read_csv(path, store)
        assert session.channels["Ground Speed"].data[1] == pytest.approx(expected)
    assert len(json.loads(store.path.read_text())["sources"]) == 3
