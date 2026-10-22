"""Resumable downloads of the preferred full daily ADSB.lol archive copies."""

import bisect
import gzip
import hashlib
import json
import math
import re
import shutil
import tarfile
import zlib
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ingestion.adsblol.diagnostics import count

RESERVE_BYTES = 2**30
MAX_BUDGET_BYTES = 20 * 2**30


def archive_groups(paths):
    """Separate a standalone tar from an ordered split tar of the same release."""
    paths = sorted(map(Path, paths))
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("Missing or duplicate daily archive parts")
    standalone = [path for path in paths if path.suffix == ".tar"]
    split = [path for path in paths if path.suffix != ".tar"]
    if (
        len(standalone) > 1
        or split
        and (
            [path.suffix for path in split] != [".a" + chr(ord("a") + n) for n in range(len(split))]
            or len({path.with_suffix("") for path in split}) != 1
        )
    ):
        raise ValueError("Daily archive parts are incomplete or out of order")
    if standalone and split and standalone[0] != split[0].with_suffix(""):
        raise ValueError("Daily archive parts belong to different releases")
    return ([standalone] if standalone else []) + ([split] if split else [])


class JoinedFiles:
    """Read split tar parts as one byte stream without extracting to disk."""

    def __init__(self, files):
        self.files = list(files)
        self.ends = []
        for source in self.files:
            size = source.seek(0, 2)
            source.seek(0)
            self.ends.append((self.ends[-1] if self.ends else 0) + size)
        self.position = 0

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset + (self.position if whence == 1 else self.ends[-1] if whence == 2 else 0)
        if position < 0 or whence not in (0, 1, 2):
            raise ValueError("Invalid archive seek")
        self.position = position
        return position

    def read(self, size=-1):
        size = (
            min(size, self.ends[-1] - self.position) if size >= 0 else self.ends[-1] - self.position
        )
        chunks = []
        while size > 0:
            index = bisect.bisect_right(self.ends, self.position)
            start = self.ends[index - 1] if index else 0
            source = self.files[index]
            source.seek(self.position - start)
            chunk = source.read(min(size, self.ends[index] - self.position))
            if not chunk:
                raise EOFError("Archive part ended before its recorded size")
            chunks.append(chunk)
            size -= len(chunk)
            self.position += len(chunk)
        return b"".join(chunks)


def _stream_traces(paths, *, diagnostics=None):
    """Yield one aircraft member at a time; never extract tar paths to disk."""
    with ExitStack() as stack:
        files = [stack.enter_context(path.open("rb")) for path in paths]
        archive = stack.enter_context(tarfile.open(fileobj=JoinedFiles(files), mode="r|"))
        for member in archive:
            count(diagnostics, "archive_members", "examined")
            if not member.isfile() or not re.fullmatch(
                r"(?:\./)?traces/[0-9a-f]{2}/trace_full_[~0-9a-f]+\.json", member.name
            ):
                count(diagnostics, "archive_members", "ignored_non_trace")
                continue
            count(diagnostics, "archive_members", "aircraft_trace")
            yield _read_trace(archive, member, diagnostics=diagnostics), member.size


def _read_trace(archive, member, *, diagnostics=None):
    try:
        with archive.extractfile(member) as compressed:
            with gzip.GzipFile(fileobj=compressed) as source:
                try:
                    data = json.load(source)
                except (ValueError, UnicodeError):
                    count(diagnostics, "invalid_records", "invalid_json_member")
                    raise
    except (OSError, EOFError, zlib.error):
        count(diagnostics, "invalid_records", "invalid_gzip_member")
        raise
    if not isinstance(data, dict):
        count(diagnostics, "invalid_records", "invalid_member_shape")
        raise ValueError("Aircraft member must be a JSON object")
    return data


def _merge_snapshots(records, *, diagnostics=None):
    """Union one aircraft's snapshots by absolute observation time.

    The snapshot spanning the longest interval supplies metadata and wins equal-time
    ties, just as the reducer keeps the first observation for a timestamp. Retain
    distinct source timestamps without fuzzy rounding or inferring new positions.
    """

    def timed(data):
        base = data.get("timestamp")
        if not isinstance(base, (int, float)) or not math.isfinite(base):
            raise ValueError("Mixed aircraft snapshot has an invalid base timestamp")
        if not isinstance(data.get("trace"), list):
            raise ValueError("Mixed aircraft snapshot has no observation list")
        result, invalid = [], []
        for row in data["trace"]:
            if (
                not isinstance(row, list)
                or not row
                or not isinstance(row[0], (int, float))
                or not math.isfinite(row[0])
            ):
                invalid.append(row)
                continue
            result.append((round(base * 1_000_000) + round(row[0] * 1_000_000), row))
        return result, invalid

    if len({data.get("icao") for data in records}) != 1:
        raise ValueError("Mixed aircraft snapshots disagree on aircraft identity")
    sources = [(data, *timed(data)) for data in records]
    sources.sort(
        key=lambda item: (
            max((t for t, _ in item[1]), default=0) - min((t for t, _ in item[1]), default=0),
            len(item[1]),
        ),
        reverse=True,
    )
    primary = sources[0][0]
    base = round(primary["timestamp"] * 1_000_000)
    merged = []
    invalid = []
    for _, rows, rejected in sources:
        for timestamp, row in rows:
            merged.append((timestamp, [(timestamp - base) / 1_000_000, *row[1:]]))
        invalid.extend(rejected)
    result = {**primary, "trace": [row for _, row in sorted(merged, key=lambda x: x[0])] + invalid}
    # Retain equal-time rows in source priority order: the existing reducer keeps
    # the first whole observation and checks exclusion categories in every row.
    flags = 0
    for data in records:
        if isinstance(data.get("dbFlags"), int):
            flags |= data["dbFlags"]
    result["dbFlags"] = flags
    types = {
        data["t"].strip().upper()
        for data in records
        if isinstance(data.get("t"), str)
        and re.fullmatch(r"[A-Z0-9]{2,8}", data["t"].strip().upper())
    }
    result["t"] = next(iter(types)) if len(types) == 1 else None
    if len(types) > 1:
        count(diagnostics, "metadata", "mixed_snapshot_type_conflict")
    count(diagnostics, "archive_members", "merged_aircraft_snapshots")
    return result


def archive_traces(paths, *, diagnostics=None):
    groups = archive_groups(paths)
    if len(groups) == 1:
        yield from _stream_traces(groups[0], diagnostics=diagnostics)
        return
    # A mixed release is two independent tar streams, not their concatenation:
    # tar EOF markers would silently hide the later archive. Index headers only,
    # then read/merge at most one aircraft from each stream at a time.
    with ExitStack() as stack:
        members = {}
        for group in groups:
            files = [stack.enter_context(path.open("rb")) for path in group]
            archive = stack.enter_context(tarfile.open(fileobj=JoinedFiles(files), mode="r:"))
            names = set()
            for member in archive:
                count(diagnostics, "archive_members", "examined")
                if not member.isfile() or not re.fullmatch(
                    r"(?:\./)?traces/[0-9a-f]{2}/trace_full_[~0-9a-f]+\.json", member.name
                ):
                    count(diagnostics, "archive_members", "ignored_non_trace")
                    continue
                name = member.name.removeprefix("./")
                if name in names:
                    count(diagnostics, "invalid_records", "duplicate_aircraft_member")
                    raise ValueError("Duplicate aircraft member; day not committed")
                names.add(name)
                members.setdefault(name, []).append((archive, member))
        for entries in members.values():
            records = []
            compressed_bytes = 0
            for archive, member in entries:
                count(diagnostics, "archive_members", "aircraft_trace")
                records.append(_read_trace(archive, member, diagnostics=diagnostics))
                compressed_bytes += member.size
            yield (
                (
                    _merge_snapshots(records, diagnostics=diagnostics)
                    if len(records) > 1
                    else records[0]
                ),
                compressed_bytes,
            )


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(path, item):
    receipt = path.with_name(path.name + ".verified.json")
    if not path.is_file() or path.stat().st_size != item["bytes"] or not receipt.is_file():
        return False
    saved = json.loads(receipt.read_text())
    expected = item["sha256"] or saved.get("sha256")
    return bool(expected) and saved.get("url") == item["url"] and sha256_file(path) == expected


def download_asset(client, item, output, *, before_write=None):
    path = output / item["date"] / item["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if verified(path, item):
        return "existing"
    partial = path.with_name(path.name + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > item["bytes"]:
        raise ValueError(f"Partial file exceeds expected length: {partial}")
    if offset < item["bytes"]:
        with client.stream(
            "GET", item["url"], headers={"Range": f"bytes={offset}-"} if offset else {}
        ) as response:
            response.raise_for_status()
            if response.status_code == 206:
                if not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("Server returned an incorrect resume offset")
                mode = "ab" if offset else "wb"
            elif response.status_code == 200:
                mode, offset = "wb", 0
            else:
                raise ValueError(f"Unexpected HTTP status {response.status_code}")
            with partial.open(mode) as target:
                for chunk in response.iter_bytes(1024 * 1024):
                    if before_write is not None:
                        before_write(len(chunk))
                    if offset + len(chunk) > item["bytes"]:
                        raise ValueError("Download exceeds published asset size")
                    if shutil.disk_usage(output).free < len(chunk) + RESERVE_BYTES:
                        raise OSError(
                            "Download stopped to preserve 1 GiB free; partial file is resumable"
                        )
                    target.write(chunk)
                    offset += len(chunk)
    if partial.stat().st_size != item["bytes"]:
        raise ValueError(f"Incomplete download: {partial}")
    digest = sha256_file(partial)
    if item["sha256"] and digest != item["sha256"]:
        raise ValueError(f"SHA-256 mismatch: {partial}; move it aside before retrying")
    partial.replace(path)
    path.with_name(path.name + ".verified.json").write_text(
        json.dumps(
            {
                "url": item["url"],
                "bytes": item["bytes"],
                "sha256": digest,
                "provider_digest_available": bool(item["sha256"]),
            },
            indent=2,
        )
        + "\n"
    )
    return "downloaded"


def validate_asset(item):
    day = date.fromisoformat(item["date"])
    name = item["name"]
    if not re.fullmatch(
        rf"v{re.escape(day.strftime('%Y.%m.%d'))}-planes-readsb-(prod|staging)-\d+(?:tmp)?\.tar(?:\.[a-z]{{2}})?",
        name,
    ):
        raise ValueError(f"Unexpected archive name: {name}")
    url = urlsplit(item["url"])
    # January 1 observations can be published in the preceding year's repository.
    repository_years = f"{day.year}|{day.year + 1}"
    if day.month == day.day == 1:
        repository_years += f"|{day.year - 1}"
    if (
        url.scheme != "https"
        or url.netloc != "github.com"
        or url.query
        or url.fragment
        or not re.fullmatch(
            rf"/adsblol/globe_history_({repository_years})/releases/download/[^/]+/{re.escape(name)}",
            url.path,
        )
    ):
        raise ValueError("Unexpected archive URL")
    if type(item["bytes"]) is not int or item["bytes"] <= 0:
        raise ValueError("Asset size must be a positive integer")
    if item["sha256"] is not None and not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
        raise ValueError("Invalid SHA-256")


def download_manifest(
    manifest, output, client=None, *, budget_bytes=MAX_BUDGET_BYTES, before_write=None
):
    if not 0 < budget_bytes <= MAX_BUDGET_BYTES:
        raise ValueError("Download budget must be at most 20 GiB")
    seen = set()
    for item in manifest["assets"]:
        validate_asset(item)
        identity = (item["date"], item["name"])
        if identity in seen:
            raise ValueError("Duplicate archive asset")
        seen.add(identity)
    output = Path(output).expanduser().resolve()
    # Reserve room for manifests and receipts before making any network request.
    existing = (
        sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
        if output.exists()
        else 0
    )
    remaining = 0
    for item in manifest["assets"]:
        path = output / item["date"] / item["name"]
        if verified(path, item):
            continue
        partial = path.with_name(path.name + ".partial")
        remaining += max(0, item["bytes"] - (partial.stat().st_size if partial.exists() else 0))
    if existing + remaining + 1024**2 > budget_bytes:
        raise OSError(
            f"Selection exceeds {budget_bytes / 2**30:.1f} GiB budget: needs {(existing + remaining) / 2**30:.1f} GiB. Select individual days; the full year cannot be retained within 20 GiB."
        )
    output.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output).free
    if remaining + RESERVE_BYTES > free:
        raise OSError(
            f"Archive needs {remaining / 2**30:.1f} GiB plus 1 GiB reserve; destination has {free / 2**30:.1f} GiB free"
        )
    (output / "download-selection.json").write_text(json.dumps(manifest, indent=2) + "\n")
    owns = client is None
    client = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(60, read=120),
        headers={"User-Agent": "Flyji-ADSB-archive/1.0"},
    )
    try:
        for number, item in enumerate(manifest["assets"], 1):
            result = download_asset(client, item, output, before_write=before_write)
            print(f"{number}/{len(manifest['assets'])} {result}: {item['name']}", flush=True)
    finally:
        if owns:
            client.close()
