"""RAM scratch redirects only disposable raw data, never durable facts."""

import json
import plistlib
from datetime import date
from pathlib import Path

import pytest
from test_adsblol_extraction import rows, run
from test_adsblol_legs import leg_trace

from core.file_lock import file_mutex
from ingestion.adsblol import scratch
from ingestion.adsblol.extraction import ExtractionBudget


@pytest.fixture
def ram(tmp_path, monkeypatch):
    mount = tmp_path / "memory"
    mount.mkdir()
    monkeypatch.setattr(scratch, "ram_mount", lambda path: Path(path).resolve())
    monkeypatch.setattr(scratch.os.path, "ismount", lambda path: Path(path) == mount)
    return mount


def test_binding_preserves_receipts_and_database_and_extracts_in_ram(tmp_path, ram):
    root = tmp_path / "durable"
    root.mkdir()
    (root / "raw" / "2025-01-01").mkdir(parents=True)
    (root / "raw" / "2025-01-01" / "download.lock").touch()
    (root / "receipt.json").write_text("keep")
    state = scratch.bind_ram(root, ram)
    assert scratch.bind_ram(root, ram) == state
    assert (root / "raw").resolve().is_relative_to(ram)
    assert (root / "raw" / "2025-01-01" / "download.lock").exists()
    receipt, item = run(root, date(2025, 1, 1), [leg_trace()])
    assert receipt["counts"]["legs_completed"] == 1
    assert len(rows(root, "itinerary_legs")) == 1
    assert (root / "itineraries.db").is_file()
    assert not (ram / "itineraries.db").exists()
    assert (root / "receipt.json").read_text() == "keep"
    assert not (root / "raw" / "2025-01-01" / item["name"]).exists()
    assert not list(ram.rglob("*.tar"))
    (root / "raw" / "partial").write_bytes(b"x" * 1024)
    assert ExtractionBudget(root).used() >= 1024
    with pytest.raises(OSError, match="working space"):
        ExtractionBudget(root, limit=1023).check()


@pytest.mark.parametrize("change", ["unmount", "marker", "device", "link"])
def test_ram_loss_stops_before_download_or_database_write(tmp_path, ram, monkeypatch, change):
    root = tmp_path / "durable"
    state = scratch.bind_ram(root, ram)
    if change == "unmount":
        monkeypatch.setattr(scratch.os.path, "ismount", lambda path: False)
    elif change == "marker":
        (Path(state["raw"]) / ".ram-session").unlink()
    elif change == "device":
        (root / scratch.CONFIG).write_text(json.dumps({**state, "device": -1}))
    else:
        (root / "raw").unlink()
        (root / "raw").mkdir()
    with pytest.raises(OSError, match="no disk fallback"):
        run(root, date(2025, 1, 1), [leg_trace()])
    assert not (root / "itineraries.db").exists()
    assert not list(root.rglob("*.tar"))


@pytest.mark.parametrize("lock", [".extract.lock", "itineraries.db.extraction.lock"])
def test_binding_refuses_active_day(tmp_path, ram, lock):
    root = tmp_path / "durable"
    with file_mutex(root / lock):
        with pytest.raises(ValueError, match="active"):
            scratch.bind_ram(root, ram)
    assert not (root / scratch.CONFIG).exists()


def test_binding_preserves_unfinished_archive_in_place(tmp_path, ram):
    root = tmp_path / "durable"
    (root / "raw").mkdir(parents=True)
    archive = root / "raw" / "archive.tar.partial"
    archive.write_text("incomplete")
    with pytest.raises(ValueError, match="Raw inputs remain"):
        scratch.bind_ram(root, ram)
    assert archive.read_text() == "incomplete"
    assert not (root / scratch.CONFIG).exists()
    assert not list(ram.iterdir())


def test_mac_probe_rejects_disk_backed_image(tmp_path, monkeypatch):
    monkeypatch.setattr(scratch.sys, "platform", "darwin")
    monkeypatch.setattr(scratch.os.path, "ismount", lambda path: True)
    data = {
        "images": [
            {"image-path": "/tmp/file.dmg", "system-entities": [{"mount-point": str(tmp_path)}]}
        ]
    }
    monkeypatch.setattr(scratch.subprocess, "check_output", lambda args: plistlib.dumps(data))
    with pytest.raises(ValueError, match="not a recognized RAM"):
        scratch.ram_mount(tmp_path)
    data["images"][0]["image-path"] = "ram://8388608"
    assert scratch.ram_mount(tmp_path) == tmp_path


def test_disk_mode_unchanged_but_unmanaged_symlink_rejected(tmp_path):
    assert scratch.raw_directory(tmp_path) == tmp_path / "raw"
    (tmp_path / "raw").symlink_to(tmp_path / "missing")
    with pytest.raises(OSError, match="no RAM configuration"):
        scratch.raw_directory(tmp_path)
