"""Tests for the run list's saved summaries: a restart of the app shouldn't read
every run again, only the ones that are new or changed."""

import json
import os

import pytest
from test_csv_import import standard_csv

from fs_telemetry import catalog as catalog_module
from fs_telemetry.catalog import Catalog


@pytest.fixture
def csv(tmp_path):
    (tmp_path / "csv").mkdir()
    return tmp_path / "csv"


@pytest.fixture
def reads(monkeypatch):
    """The files the catalog reads in full, in order."""
    read = []
    real = catalog_module.read_session

    def counting(path):
        read.append(path.name)
        return real(path)

    monkeypatch.setattr(catalog_module, "read_session", counting)
    return read


def start(tmp_path, csv):
    """The app starting: a new catalog, with nothing in memory."""
    return Catalog(root=tmp_path / "none", iracing_root=None, csv_root=csv, csp_root=None)


def test_a_restart_uses_the_saved_summaries(tmp_path, csv, reads):
    standard_csv(csv / "run1.csv")
    standard_csv(csv / "run2.csv")
    first = start(tmp_path, csv).summaries()
    assert sorted(reads) == ["run1.csv", "run2.csv"]
    assert catalog_module.CACHE_FILE.exists()

    reads.clear()
    assert start(tmp_path, csv).summaries() == first
    assert reads == []


def test_a_changed_run_is_read_again(tmp_path, csv, reads):
    standard_csv(csv / "run1.csv")
    standard_csv(csv / "run2.csv")
    start(tmp_path, csv).summaries()
    reads.clear()
    standard_csv(csv / "run2.csv", laps=5.5)
    os.utime(csv / "run2.csv", ns=(10**9, 10**9))  # a different time, however fast the test runs
    (s,) = [s for s in start(tmp_path, csv).summaries() if s.id == "run2"]
    assert reads == ["run2.csv"]
    assert s.lap_count == 6


def test_new_and_deleted_runs(tmp_path, csv, reads):
    standard_csv(csv / "run1.csv")
    start(tmp_path, csv).summaries()
    reads.clear()
    standard_csv(csv / "run2.csv")
    (csv / "run1.csv").unlink()
    assert [s.id for s in start(tmp_path, csv).summaries()] == ["run2"]
    assert reads == ["run2.csv"]
    saved = json.loads(catalog_module.CACHE_FILE.read_text())["runs"]
    assert [os.path.basename(p) for p in saved] == ["run2.csv"]


def test_a_new_version_of_the_app_reads_every_run_again(tmp_path, csv, reads, monkeypatch):
    standard_csv(csv / "run1.csv")
    start(tmp_path, csv).summaries()
    reads.clear()
    monkeypatch.setattr(catalog_module, "summaries_version", lambda: "something else")
    start(tmp_path, csv).summaries()
    assert reads == ["run1.csv"]


@pytest.mark.parametrize("saved", ["", "not json", "[]", '{"version": 1}', '{"runs": {"x": 1}}'])
def test_a_damaged_save_is_ignored(tmp_path, csv, reads, saved):
    standard_csv(csv / "run1.csv")
    catalog_module.CACHE_FILE.write_text(saved)
    assert [s.id for s in start(tmp_path, csv).summaries()] == ["run1"]
    assert reads == ["run1.csv"]
    reads.clear()
    start(tmp_path, csv).summaries()
    assert reads == []  # and saved again properly


def test_an_unreadable_run_isnt_tried_again_after_a_restart(tmp_path, csv, reads):
    standard_csv(csv / "run1.csv")
    (csv / "broken.csv").write_text("just one line\n")
    assert [s.id for s in start(tmp_path, csv).summaries()] == ["run1"]
    start(tmp_path, csv).summaries()  # reading a new kind of CSV saves its mapping: settled now
    reads.clear()
    assert [s.id for s in start(tmp_path, csv).summaries()] == ["run1"]
    assert reads == []


def test_changing_the_csv_mappings_reads_csv_runs_again(tmp_path, csv, reads):
    standard_csv(csv / "run1.csv")
    start(tmp_path, csv).summaries()
    start(tmp_path, csv).summaries()  # reading a new kind of CSV saves its mapping: settled now
    reads.clear()
    mappings = catalog_module.csv_import.MAPPINGS_FILE
    mappings.write_text(mappings.read_text())
    os.utime(mappings, ns=(10**9, 10**9))
    start(tmp_path, csv).summaries()
    assert reads == ["run1.csv"]


def test_nothing_is_written_when_nothing_changed(tmp_path, csv):
    standard_csv(csv / "run1.csv")
    catalog = start(tmp_path, csv)
    catalog.summaries()
    start(tmp_path, csv).summaries()  # reading a new kind of CSV saves its mapping: settled now
    os.utime(catalog_module.CACHE_FILE, ns=(10**9, 10**9))
    catalog.summaries()
    start(tmp_path, csv).summaries()
    assert catalog_module.CACHE_FILE.stat().st_mtime_ns == 10**9
