"""Tests for the team library: sharing runs through a folder the team syncs.

Everything happens in temporary folders: "my PC", "a teammate's folder" and the
team library itself are all made up per test."""

import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient
from test_csv_import import standard_csv

from fs_telemetry import main
from fs_telemetry.catalog import Catalog
from fs_telemetry.shared import SharedLibrary, folder_name, summary_path


@pytest.fixture
def team(tmp_path):
    (tmp_path / "team").mkdir()
    return tmp_path / "team"


def pc(tmp_path, team, me="nicolas", name="pc"):
    """A PC with its own CSV folder (which may have runs in it), using the team library."""
    csv = tmp_path / name / "csv"
    csv.mkdir(parents=True, exist_ok=True)
    return Catalog(root=tmp_path / name / "none", iracing_root=None, csv_root=csv, csp_root=None,
                   shared=SharedLibrary(team, me)), csv


def test_without_a_team_folder_runs_are_just_mine(tmp_path):
    catalog, csv = pc(tmp_path, None)
    standard_csv(csv / "run1.csv")
    (s,) = catalog.summaries()
    assert (s.id, s.owner, s.mine, s.shared) == ("run1", "nicolas", True, False)
    assert not catalog.shared.enabled
    with pytest.raises(ValueError, match="FS_SHARED_DIR"):
        catalog.share("run1")


def test_a_shared_run_is_copied_with_its_summary_and_listed_once(tmp_path, team):
    catalog, csv = pc(tmp_path, team)
    standard_csv(csv / "run1.csv")
    s = catalog.share("run1")
    assert (s.id, s.mine, s.shared) == ("run1", True, True)
    copy = team / "nicolas" / "runs" / "run1.csv"
    assert copy.read_bytes() == (csv / "run1.csv").read_bytes()
    assert json.loads(summary_path(copy).read_text())["key"] == s.key
    assert [x.id for x in catalog.summaries()] == ["run1"]  # not twice
    assert catalog.share("run1") == s  # sharing again changes nothing


def test_teammates_see_a_shared_run_and_can_open_it(tmp_path, team):
    mine, csv = pc(tmp_path, team)
    standard_csv(csv / "run1.csv")
    shared = mine.share("run1")
    theirs, _ = pc(tmp_path, team, me="andreas", name="their pc")
    (s,) = theirs.summaries()
    assert (s.id, s.owner, s.mine, s.shared, s.key) == ("nicolas~run1", "nicolas", False, True, shared.key)
    assert s.best_lap_s == shared.best_lap_s
    assert theirs.get("nicolas~run1").laps == mine.get("run1").laps
    with pytest.raises(ValueError, match="your own"):
        theirs.share("nicolas~run1")
    with pytest.raises(ValueError, match="your own"):
        theirs.unshare("nicolas~run1")


def test_my_runs_shared_from_another_pc_are_still_mine(tmp_path, team):
    desktop, csv = pc(tmp_path, team)
    standard_csv(csv / "run1.csv")
    desktop.share("run1")
    laptop, _ = pc(tmp_path, team, name="laptop")
    (s,) = laptop.summaries()
    assert (s.id, s.mine, s.shared) == ("nicolas~run1", True, True)


def test_listing_reads_the_summary_not_the_run(tmp_path, team):
    """OneDrive only downloads a run when it's opened: listing must not open it."""
    mine, csv = pc(tmp_path, team)
    standard_csv(csv / "run1.csv")
    mine.share("run1")
    (team / "nicolas" / "runs" / "run1.csv").write_text("not a run any more")  # opening it would fail
    theirs, _ = pc(tmp_path, team, me="andreas", name="their pc")
    assert [s.id for s in theirs.summaries()] == ["nicolas~run1"]


def test_a_run_copied_in_by_hand_is_read_instead(tmp_path, team):
    (team / "andreas" / "runs").mkdir(parents=True)
    standard_csv(team / "andreas" / "runs" / "by hand.csv")
    catalog, _ = pc(tmp_path, team)
    (s,) = catalog.summaries()
    assert (s.id, s.owner, s.mine, s.lap_count > 1) == ("andreas~by hand", "andreas", False, True)


def test_a_teammates_run_i_also_have_is_listed_once_as_theirs(tmp_path, team):
    theirs, their_csv = pc(tmp_path, team, me="andreas", name="their pc")
    standard_csv(their_csv / "run1.csv")
    theirs.share("run1")
    mine, csv = pc(tmp_path, team)
    shutil.copy2(their_csv / "run1.csv", csv / "run1.csv")  # sent the old way
    (s,) = mine.summaries()
    assert (s.id, s.owner, s.mine, s.shared) == ("run1", "andreas", False, True)


def test_unsharing_takes_the_copy_out_and_keeps_mine(tmp_path, team):
    catalog, csv = pc(tmp_path, team)
    standard_csv(csv / "run1.csv")
    catalog.share("run1")
    s = catalog.unshare("run1")
    assert (s.id, s.shared) == ("run1", False)
    assert list((team / "nicolas" / "runs").iterdir()) == []
    assert (csv / "run1.csv").exists()


def test_folder_names_are_safe():
    assert folder_name("Nicolas P") == "Nicolas P"
    assert folder_name('a/b\\c:d~e?') == "a_b_c_d_e_"
    assert folder_name("  ") == "unknown"


def test_api(tmp_path, team, monkeypatch):
    catalog, csv = pc(tmp_path, team)
    standard_csv(csv / "run1.csv")
    monkeypatch.setattr(main, "catalog", catalog)
    client = TestClient(main.app)
    assert client.get("/api/team").json() == {"enabled": True, "folder": str(team), "me": "nicolas", "people": []}
    r = client.post("/api/sessions/run1/share")
    assert r.status_code == 200 and r.json()["shared"] is True
    assert client.get("/api/team").json()["people"] == ["nicolas"]
    assert client.get("/api/sessions").json()[0]["owner"] == "nicolas"
    assert client.delete("/api/sessions/run1/share").json()["shared"] is False
    assert client.delete("/api/sessions/run1/share").status_code == 422  # not shared any more
    assert client.post("/api/sessions/nope/share").status_code == 404


@pytest.mark.skipif(os.name != "nt", reason="Windows' 260-character path limit")
def test_paths_over_windows_limit_still_work(tmp_path):
    """Synced Teams folders are deep and AC file names long: the summary next to a
    run can go past 260 characters."""
    team = tmp_path / ("deep folder " * 12).strip()
    team.mkdir()
    catalog, csv = pc(tmp_path, team)
    name = "24112025-215342-02BSUFSTPAPACHRISTOPHOROU-madformulateam_mft02_hy-micevec_vfsaa"
    standard_csv(csv / f"{name}.csv")
    assert len(str(summary_path(team / "nicolas" / "runs" / f"{name}.csv"))) > 260
    assert catalog.share(name).shared
    theirs, _ = pc(tmp_path, team, me="andreas", name="their pc")
    (s,) = theirs.summaries()
    assert s.id == f"nicolas~{name}" and theirs.get(s.id).laps
