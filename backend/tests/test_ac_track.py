"""Tests for reading AC fast_lane.ai files and lining them up with telemetry."""

import struct

import numpy as np
import pytest

from fs_telemetry.ingest.ac_track import align_to_lap, canonical_track, find_layouts, read_fast_lane, track_aliases

R = 100.0  # circle radius, m
N = 400


def write_fake_ai(path, side_left=4.0, side_right=6.0, counter_clockwise=True):
    """A version-7 fast_lane.ai for a circle in the x-z plane."""
    a = np.linspace(0, 2 * np.pi, N, endpoint=False) * (1 if counter_clockwise else -1)
    x, z = R * np.cos(a), R * np.sin(a)
    buf = bytearray(struct.pack("<4i", 7, N, 0, 0))
    for i in range(N):
        buf += struct.pack("<4fi", x[i], 0.0, z[i], i * 2 * np.pi * R / N, i)
    buf += struct.pack("<i", N)
    for _ in range(N):
        extra = [0.0] * 18
        extra[5], extra[6] = side_left, side_right
        buf += struct.pack("<18f", *extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buf))
    return x, z


def test_edges_are_offset_by_the_side_distances(tmp_path):
    f = tmp_path / "t" / "ai" / "fast_lane.ai"
    write_fake_ai(f, side_left=4, side_right=6, counter_clockwise=True)
    g = read_fast_lane(f)
    # Driving anticlockwise, the inside of the circle is on the anticlockwise side;
    # AC's "left" is the clockwise side (see ac_track.py), so it's the OUTSIDE here.
    np.testing.assert_allclose(np.hypot(g.left_x, g.left_z), R + 4, atol=0.05)
    np.testing.assert_allclose(np.hypot(g.right_x, g.right_z), R - 6, atol=0.05)
    assert g.length == pytest.approx(2 * np.pi * R * (N - 1) / N, rel=1e-4)


def test_wrong_version_is_refused(tmp_path):
    f = tmp_path / "old.ai"
    f.write_bytes(struct.pack("<4i", 3, 0, 0, 0))
    with pytest.raises(ValueError, match="version 3"):
        read_fast_lane(f)


def test_layouts_are_found(tmp_path):
    write_fake_ai(tmp_path / "trk" / "short" / "ai" / "fast_lane.ai")
    write_fake_ai(tmp_path / "trk" / "long" / "ai" / "fast_lane.ai")
    assert sorted(find_layouts([tmp_path], "trk")) == ["trk/long", "trk/short"]
    assert find_layouts([tmp_path], "missing") == {}


def test_alignment_finds_the_axis_mapping_and_rejects_other_tracks(tmp_path):
    # An off-centre ellipse so every mapping gives a different picture
    f = tmp_path / "trk" / "ai" / "fast_lane.ai"
    x, z = write_fake_ai(f)
    g = read_fast_lane(f, "trk")
    g.x, g.z = g.x * 1.6 + 40, g.z + 15
    # Telemetry that uses X = x, Y = -z, with a car 1-2 m off the AI line
    rng = np.random.default_rng(0)
    lap_x, lap_y = g.x + rng.normal(0, 1, N), -g.z + rng.normal(0, 1, N)
    a = align_to_lap({"trk": g}, lap_x, lap_y)
    assert a.mapping == "x,-z" and a.median_gap_m < 2.5
    # A lap from somewhere else entirely doesn't match
    assert align_to_lap({"trk": g}, lap_x + 500, lap_y) is None


def _install(root, track, *layouts):
    """A track folder with layout sub-folders (each with data/, as AC's have), plus
    the ui/ folder every track has."""
    (root / track / "ui").mkdir(parents=True)
    for layout in layouts:
        (root / track / layout / "data").mkdir(parents=True)


def test_one_name_per_track(tmp_path):
    _install(tmp_path, "sportsland_sugo_mts", "sugo", "sugo_west")
    _install(tmp_path, "micevec_vfsaa", "vfsaa", "vfsaa25")
    dirs = [tmp_path / "not_there", tmp_path]
    assert canonical_track("sportsland_sugo_mts", dirs, {}) == "sportsland_sugo_mts"
    # Some loggers stick the layout straight on the end of the folder name
    assert canonical_track("sportsland_sugo_mtssugo", dirs, {}) == "sportsland_sugo_mts"
    assert canonical_track("sportsland_sugo_mtssugo_west", dirs, {}) == "sportsland_sugo_mts"
    # ...but only a real layout counts, and a name that is a folder is left alone
    assert canonical_track("sportsland_sugo_mtsui", dirs, {}) == "sportsland_sugo_mtsui"
    assert canonical_track("micevec_vfsaa", dirs, {}) == "micevec_vfsaa"
    # Not installed here: left as the file has it, unless an alias says otherwise
    assert canonical_track("elsewherelayout", dirs, {}) == "elsewherelayout"
    assert canonical_track("elsewherelayout", dirs, {"elsewherelayout": "elsewhere"}) == "elsewhere"
    assert canonical_track("", dirs, {}) == ""


def test_aliases_come_from_the_data_folder(tmp_path):
    f = tmp_path / "track_aliases.json"
    assert track_aliases(f) == {}  # none yet
    f.write_text('{"sugo2": "sportsland_sugo_mts"}', encoding="utf-8")
    assert track_aliases(f) == {"sugo2": "sportsland_sugo_mts"}
    f.write_text("not json", encoding="utf-8")
    assert track_aliases(f) == {}
