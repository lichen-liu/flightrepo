"""Bind disposable raw archives to an independently mounted RAM filesystem.

Mount creation is an explicit host operation; databases and receipts stay durable.
The day lock also allows a frozen older worker to switch without interruption.
"""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from core.file_lock import file_mutex

CONFIG = ".raw-storage.json"


def ram_mount(mount):
    """Prove RAM backing rather than accepting an arbitrary external disk."""
    mount = Path(mount).resolve(strict=True)
    if not os.path.ismount(mount):
        raise ValueError("Raw destination must be a mounted RAM filesystem")
    if sys.platform == "darwin":
        data = plistlib.loads(subprocess.check_output(["hdiutil", "info", "-plist"]))
        if any(
            image.get("image-path", "").startswith("ram://")
            and any(
                entity.get("mount-point") == str(mount)
                for entity in image.get("system-entities", [])
            )
            for image in data.get("images", [])
        ):
            return mount
    elif sys.platform.startswith("linux"):
        # mountinfo escapes whitespace/backslashes with octal sequences.
        import re

        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields, filesystem = line.split(" - ", 1)
            target = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields.split()[4])
            if target == str(mount) and filesystem.split()[0] == "tmpfs":
                return mount
    raise ValueError("Destination is not a recognized RAM disk (macOS ram:// or Linux tmpfs)")


def raw_directory(root):
    """Fail closed if the configured RAM mount/link was lost or replaced."""
    root = Path(root)
    raw = root / "raw"
    config = root / CONFIG
    if not config.exists():
        if raw.is_symlink():
            raise OSError("Raw symlink has no RAM configuration; configure RAM storage first")
        return raw
    state = json.loads(config.read_text())
    mount = Path(state["mount"])
    target = Path(state["raw"])
    marker = target / ".ram-session"
    if (
        not os.path.ismount(mount)
        or mount.stat().st_dev != state["device"]
        or not raw.is_symlink()
        or raw.resolve() != target
        or not marker.is_file()
        or marker.read_text() != state["session"]
    ):
        raise OSError(
            "RAM raw storage is unavailable or changed; remount and reconfigure, no disk fallback"
        )
    return raw


def bind_ram(root, mount, *, output_db=None, wait=False):
    """Switch only between completed days; refuse to move any unfinished input."""
    root = Path(root).expanduser().resolve()
    mount = ram_mount(mount)
    if root == mount or mount in root.parents or root in mount.parents:
        raise ValueError("Durable data root and RAM mount must be separate")
    root.mkdir(parents=True, exist_ok=True)
    database = Path(output_db).resolve() if output_db else root / "itineraries.db"
    lock = database.with_name(database.name + ".extraction.lock")
    with file_mutex(root / ".extract.lock", blocking=wait) as acquired:
        if not acquired:
            raise ValueError("Extraction day active; retry between days or use --wait")
        with file_mutex(lock, blocking=False) as acquired:
            if not acquired:
                raise ValueError("Output database is active in another extraction")
            if (root / CONFIG).exists():
                raw_directory(root)
                state = json.loads((root / CONFIG).read_text())
                if state["mount"] != str(mount):
                    raise ValueError("A different RAM mount is already configured")
                return state
            raw, backup = root / "raw", root / "raw.disk-before-ram"
            if raw.is_symlink() or backup.exists():
                raise ValueError("Unmanaged raw link or previous switch backup requires inspection")
            if raw.exists() and any(
                path.is_file()
                and not (path.name.endswith(".lock") or path.name == "download-selection.json")
                for path in raw.rglob("*")
            ):
                raise ValueError("Raw inputs remain; finish/resume the day before switching")
            session = uuid4().hex
            target = mount / (root.name + "-" + session)
            state = {
                "version": 1,
                "mount": str(mount),
                "raw": str(target),
                "device": mount.stat().st_dev,
                "session": session,
                "capacity_bytes": shutil.disk_usage(mount).total,
                "database": str(database),
            }
            target.mkdir()
            try:
                if raw.exists():
                    shutil.copytree(raw, target, dirs_exist_ok=True)
                (target / ".ram-session").write_text(session)
                if raw.exists():
                    raw.rename(backup)
                raw.symlink_to(target, target_is_directory=True)
                (root / CONFIG).write_text(json.dumps(state, indent=2) + "\n")
                raw_directory(root)
            except BaseException:
                (root / CONFIG).unlink(missing_ok=True)
                if raw.is_symlink():
                    raw.unlink()
                if backup.exists():
                    backup.rename(raw)
                shutil.rmtree(target)
                raise
            return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--mount", type=Path, required=True)
    parser.add_argument("--output-db", type=Path)
    parser.add_argument(
        "--wait", action="store_true", help="wait for the current day to commit/clean up"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            bind_ram(args.data_root, args.mount, output_db=args.output_db, wait=args.wait), indent=2
        )
    )


if __name__ == "__main__":
    main()
