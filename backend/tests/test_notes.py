"""Session notes, tags, stars and lap flags. Each test gets its own notes file in a
temp folder, and a stand-in for the run list, so no real data is touched."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fs_telemetry import main
from fs_telemetry.catalog import session_key
from fs_telemetry.ingest.motec_ld import LdSession
from fs_telemetry.notes import NotesStore, clean_tags


@dataclass
class FakeSummary:
    id: str
    key: str
    source: str = "ac"
    driver: str = "Papa"
    car: str = "madformulateam_mft02"
    track: str = "silverstone"
    started: datetime | None = datetime(2026, 10, 1, 14, 30)
    lap_count: int = 6


class FakeCatalog:
    def __init__(self, *runs):
        self.runs = {r.id: r for r in runs}

    def summary(self, session_id):
        return self.runs.get(session_id)


@pytest.fixture
def store(tmp_path):
    return NotesStore(tmp_path / "session-notes.json")


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(main, "session_notes", store)
    # two exports of the same run (same key), and another run
    monkeypatch.setattr(main, "catalog", FakeCatalog(FakeSummary("run_a", "k1"), FakeSummary("run_a_again", "k1"),
                                                     FakeSummary("run_b", "k2", track="spa")))
    return TestClient(main.app)


def _session(path, **kw):
    base = dict(path=Path(path), driver="Papa", car="mft02", track="spa", date="01/10/2026", time="14:30:00", channels={})
    return LdSession(**{**base, **kw})


def test_the_key_ignores_the_file_name_but_not_the_run():
    a = _session("exports/run1.ld")
    assert session_key(a) == session_key(_session("elsewhere/renamed_copy.ld"))  # re-exported / moved / renamed
    assert session_key(a) != session_key(_session("run1.ld", time="14:31:00"))  # a different run
    assert session_key(a) != session_key(_session("run1.ld", driver="Someone else"))
    assert session_key(a) != session_key(_session("run1.ld", source="iracing"))
    assert session_key(_session("x/odd.ld", date="", time="")) == "file-odd"  # no start time: fall back to the file


def test_tags_are_tidied():
    assert clean_tags([" Race  Sim", "race sim", "WET", "", "quali"]) == ["race sim", "wet", "quali"]


def test_notes_tags_and_star_are_saved_and_survive_a_restart(client, store):
    r = client.patch("/api/sessions/run_a/notes", json={"notes": "new rear wing, cold tyres", "tags": ["Baseline"], "star": True})
    assert r.status_code == 200
    assert r.json()["tags"] == ["baseline"] and r.json()["track"] == "silverstone"
    client.patch("/api/sessions/run_a/notes", json={"star": False})  # only the fields sent change
    n = NotesStore(store.path).get("k1")  # as if the app had restarted
    assert n.notes == "new rear wing, cold tyres" and n.tags == ["baseline"] and not n.star


def test_notes_follow_the_run_not_the_file(client):
    client.patch("/api/sessions/run_a/notes", json={"notes": "good run"})
    assert list(client.get("/api/notes").json()) == ["k1"]  # filed under the run's key...
    client.patch("/api/sessions/run_a_again/notes", json={"tags": ["quali"]})  # ...so the re-export shares them
    n = client.get("/api/notes").json()["k1"]
    assert n["notes"] == "good run" and n["tags"] == ["quali"]


def test_lap_notes_and_flags(client, store):
    client.patch("/api/sessions/run_a/laps/4/notes", json={"note": "flat spot", "flag": "ignore"})
    client.patch("/api/sessions/run_a/laps/2/notes", json={"flag": "use"})
    assert store.flags("k1") == {4: "ignore", 2: "use"}
    client.patch("/api/sessions/run_a/laps/4/notes", json={"flag": None})  # back to automatic, note kept
    assert store.flags("k1") == {2: "use"} and store.get("k1").laps[4].note == "flat spot"
    assert client.patch("/api/sessions/run_a/laps/9/notes", json={"flag": "use"}).status_code == 404
    assert client.patch("/api/sessions/run_a/laps/2/notes", json={"flag": "maybe"}).status_code == 422
    assert client.patch("/api/sessions/nope/laps/2/notes", json={"flag": "use"}).status_code == 404


def test_a_run_with_nothing_left_is_dropped_from_the_file(client, store):
    client.patch("/api/sessions/run_b/notes", json={"star": True})
    client.patch("/api/sessions/run_b/notes", json={"star": False})
    assert store.all() == {}


def test_tag_suggestions_put_used_tags_first(client):
    client.patch("/api/sessions/run_a/notes", json={"tags": ["wet", "long run"]})
    client.patch("/api/sessions/run_b/notes", json={"tags": ["wet"]})
    tags = client.get("/api/notes/tags").json()
    assert tags[:2] == ["wet", "long run"] and "baseline" in tags and tags.count("wet") == 1


def test_export_and_import_merge_by_newest_edit(client, store, tmp_path):
    client.patch("/api/sessions/run_a/notes", json={"notes": "mine"})
    exported = client.get("/api/notes/export")
    assert exported.headers["content-disposition"].startswith("attachment")
    data = exported.json()

    other = NotesStore(tmp_path / "other.json")  # another PC
    assert other.import_file(data).added == 1
    assert other.get("k1").notes == "mine"

    # the other PC edits it later: importing that back replaces ours; importing ours again keeps theirs
    theirs = data["sessions"]["k1"] | {"notes": "theirs", "updated": "2999-01-01T00:00:00+00:00"}
    r = client.post("/api/notes/import", json=data | {"sessions": {"k1": theirs, "k9": theirs}}).json()
    assert r == {"added": 1, "updated": 1, "kept": 0}
    assert store.get("k1").notes == "theirs" and store.get("k9").key == "k9"
    assert client.post("/api/notes/import", json=data).json()["kept"] == 1
    assert client.post("/api/notes/import", json={"something": "else"}).status_code == 422


def test_edits_at_the_same_time_are_all_kept(store):
    from concurrent.futures import ThreadPoolExecutor

    from fs_telemetry.notes import LapNoteIn

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda lap: store.update_lap("k1", {}, lap, LapNoteIn(note=f"lap {lap}")), range(1, 25)))
    assert len(store.get("k1").laps) == 24


def test_cleared_notes_are_not_resurrected_by_an_old_export(client, store, tmp_path):
    client.patch("/api/sessions/run_a/notes", json={"notes": "old note", "star": True})
    old = store.export()
    client.patch("/api/sessions/run_a/notes", json={"notes": "", "star": False})
    assert store.all() == {}
    assert store.import_file(old).kept == 1
    assert store.all() == {}
    other = NotesStore(tmp_path / "second-pc.json")
    other.import_file(old)
    other.import_file(store.export())
    assert other.all() == {}
