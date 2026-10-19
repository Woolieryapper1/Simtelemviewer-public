"""Tests for finding the default folders."""

import os

import pytest

from fs_telemetry.config import documents_folder, first_existing


def test_first_existing_folder_wins(tmp_path):
    missing, there = tmp_path / "missing", tmp_path / "there"
    there.mkdir()
    assert first_existing(missing, there) == there
    assert first_existing(missing, tmp_path / "also missing") == missing  # none: show the first place it looked


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_documents_folder_is_found_on_windows():
    assert documents_folder().is_dir()
