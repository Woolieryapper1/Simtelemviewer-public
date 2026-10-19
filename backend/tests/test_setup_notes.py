"""Setup notes: the dated log per setup, its Markdown copies and the CSV for Excel.
Each test gets an empty library in a temp folder, so the real data folder is never touched."""

import csv
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from fs_telemetry import main
from fs_telemetry.setup_notes import GENERATED, INDEX_NAME, SetupNoteIn, SetupNotes
from fs_telemetry.setups import SetupLibrary

BASE = """[PRESSURE_LF]
VALUE=12

[ARB_FRONT]
VALUE=1

[CAR]
MODEL=madformulateam_mft02
"""
SOFTER = BASE.replace("[ARB_FRONT]\nVALUE=1", "[ARB_FRONT]\nVALUE=0")


@pytest.fixture
def library(tmp_path):
    return SetupLibrary(tmp_path / "setups" / "library.json", tmp_path / "no_ac", tmp_path / "no_iracing")


@pytest.fixture
def client(library, monkeypatch):
    monkeypatch.setattr(main, "setup_library", library)
    return TestClient(main.app)


def upload(client, ini, name="base", notes=""):
    r = client.post("/api/setups", json={"name": name, "track": "generic", "ini_text": ini, "notes": notes})
    assert r.status_code == 200
    return r.json()["id"]


def test_entries_are_saved_newest_first_and_can_be_edited_and_deleted(client):
    sid = upload(client, BASE)
    first = client.post(f"/api/setups/{sid}/notes", json={"date": "2026-10-01", "author": "Nico", "changed": "Baseline"}).json()
    client.post(f"/api/setups/{sid}/notes", json={"date": "2026-10-05", "feedback": "Understeer in slow corners"})
    entries = client.get("/api/setups/notes").json()["entries"][sid]
    assert [e["date"] for e in entries] == ["2026-10-05", "2026-10-01"]

    r = client.put(f"/api/setups/{sid}/notes/{first['id']}", json={"date": "2026-10-01", "author": "Nico", "changed": "Baseline", "why": "Start point"})
    assert r.json()["why"] == "Start point" and r.json()["added"] == first["added"]
    assert client.delete(f"/api/setups/{sid}/notes/{first['id']}").status_code == 204
    assert len(client.get("/api/setups/notes").json()["entries"][sid]) == 1
    assert client.delete(f"/api/setups/{sid}/notes/{first['id']}").status_code == 404
    assert client.post("/api/setups/nope/notes", json={"changed": "x"}).status_code == 404


def test_a_missing_or_bad_date_means_today(library):
    sid = library.add("base", "car", "generic", {"A": 1}, "upload").id
    notes = SetupNotes(library)
    assert notes.add(sid, SetupNoteIn(date="", changed="x")).date == date.today().isoformat()
    assert notes.add(sid, SetupNoteIn(date="someday", changed="x")).date == date.today().isoformat()


def test_changes_from_the_previous_version(client):
    v1 = upload(client, BASE)
    v2 = upload(client, SOFTER)  # same name: version 2
    assert client.get(f"/api/setups/{v1}/changes").json() == []
    assert client.get(f"/api/setups/{v2}/changes").json() == [
        {"label": "Arb front", "group": "Anti-roll bars", "before": 1, "after": 0}]


def test_markdown_is_written_per_setup_with_properties(client, library):
    v1 = upload(client, BASE, notes="Team baseline")
    v2 = upload(client, SOFTER)
    client.post(f"/api/setups/{v2}/notes", json={"date": "2026-10-05", "author": "Nico", "changed": "Softer front bar",
                                                  "why": "Understeer", "feedback": "Better turn-in\nbut lazy exit"})
    folder = library.path.with_name("notes")
    assert client.get("/api/setups/notes").json()["folder"] == str(folder)
    md = (folder / "madformulateam_mft02 - generic - base.md").read_text(encoding="utf-8")
    assert md.startswith("---\ntitle: \"base\"\ntype: setup\n")  # YAML properties (Obsidian, GitHub)
    assert GENERATED in md and "versions: 2" in md and f"updated: {date.today().isoformat()}" in md  # latest of all dates
    assert md.index("## v2") < md.index("## v1")  # newest version first
    assert "- Arb front: 1 → 0" in md
    assert "### 2026-10-05 · Nico" in md and "- **Why:** Understeer" in md
    assert "- **Driver feedback:** Better turn-in\n    but lazy exit" in md  # stays inside its list item
    assert "Team baseline" in md  # the version's own description
    index = (folder / INDEX_NAME).read_text(encoding="utf-8")
    assert "[base](madformulateam_mft02%20-%20generic%20-%20base.md)" in index and "1 note" in index

    # deleting both versions removes the file the app wrote, but never other files in the folder
    (folder / "my own notes.md").write_text("mine", encoding="utf-8")
    client.delete(f"/api/setups/{v1}")
    client.delete(f"/api/setups/{v2}")
    assert not (folder / "madformulateam_mft02 - generic - base.md").exists()
    assert (folder / "my own notes.md").exists()
    assert client.get("/api/setups/notes").json()["entries"] == {}


def test_editing_the_description_updates_the_markdown(client, library):
    sid = upload(client, BASE)
    client.patch(f"/api/setups/{sid}", json={"notes": "Wet setup"})
    assert "Wet setup" in (library.path.with_name("notes") / "madformulateam_mft02 - generic - base.md").read_text(encoding="utf-8")


def test_csv_opens_in_excel(client):
    v1 = upload(client, BASE, notes="Only a description")
    v2 = upload(client, SOFTER)
    client.post(f"/api/setups/{v2}/notes", json={"date": "2026-10-05", "changed": "Softer front bar, ±1 click", "feedback": "Better"})
    r = client.get("/api/setups/notes.csv")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.content.startswith("﻿".encode())  # byte-order mark: Excel reads accents and ± correctly
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:5] == ["Date", "Car", "Track", "Setup", "Version"]
    by_version = {r[4]: r for r in rows[1:]}
    assert by_version["2"][:4] == ["2026-10-05", "madformulateam_mft02", "generic", "base"]
    assert by_version["2"][6] == "Softer front bar, ±1 click"
    assert by_version["1"][9] == "Only a description"  # a version with a description but no entries
    assert [r[0] for r in rows[1:]] == sorted((r[0] for r in rows[1:]), reverse=True)  # newest first
    only_v1 = list(csv.reader(io.StringIO(client.get("/api/setups/notes.csv", params={"id": v1}).content.decode("utf-8-sig"))))
    assert len(only_v1) == 2


def test_exports_do_not_overwrite_personal_markdown_or_colliding_families(library):
    folder = library.path.with_name("notes")
    folder.mkdir(parents=True)
    personal = folder / "car - track - base.md"
    personal.write_text("My own setup notes", encoding="utf-8")
    index = folder / INDEX_NAME
    index.write_text("My own index", encoding="utf-8")
    # This mentions the marker in prose but is not an app-owned file.
    quoted = folder / "quoted.md"
    quoted.write_text(GENERATED, encoding="utf-8")
    store = SetupNotes(library)
    sid = library.add("base", "car", "track", {"A": 1}, "upload").id
    store.add(sid, SetupNoteIn(changed="baseline"))
    assert personal.read_text(encoding="utf-8") == "My own setup notes"
    assert index.read_text(encoding="utf-8") == "My own index"
    assert quoted.exists()
    for name in ("a:b", "a?b"):
        sid = library.add(name, "car", "track", {"A": 1}, "upload").id
        store.add(sid, SetupNoteIn(changed=name))
    exports = [p.read_text(encoding="utf-8") for p in folder.glob("*.md") if store._owned(p)]
    assert len(exports) == 3
    assert any('title: "a:b"' in text for text in exports)
    assert any('title: "a?b"' in text for text in exports)
