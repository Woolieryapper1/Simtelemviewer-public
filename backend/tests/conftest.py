"""Shared test set-up."""

import pytest


@pytest.fixture(autouse=True)
def csv_mappings_in_temp_folder(tmp_path, monkeypatch):
    """Reading a CSV saves its column mapping in the app's data folder. Tests must
    never write into the real one, so point it at a temporary folder."""
    from fs_telemetry.ingest import csv_import

    monkeypatch.setattr(csv_import, "MAPPINGS_FILE", tmp_path / "csv_mappings.json")
