"""Prepare raw archive metadata and an immutable airport snapshot, without flight downloads."""

import json
import re
import shutil
from collections import defaultdict
from contextlib import nullcontext
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import httpx

from ingestion.adsblol.archive import RESERVE_BYTES, validate_asset
from ingestion.adsblol.filters import DEFAULT_FILTER_PATH, CommercialFilter
from ingestion.adsblol.legs import airport_points

AIRPORT_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"


def _download(client, url, limit=32 * 1024**2):
    with client.stream("GET", url) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            if len(data) + len(chunk) > limit:
                raise ValueError("Preparation response exceeds its size limit")
            data.extend(chunk)
    return bytes(data)


def _assets(client, start, end):
    choices = {}
    years = [start.year, end.year, end.year + 1]
    if start.month == start.day == 1:
        years.append(start.year - 1)
    for year in dict.fromkeys(years):
        url = f"https://raw.githubusercontent.com/adsblol/globe_history_{year}/main/PREFERRED_RELEASES.txt"
        try:
            text = _download(client, url, 1024**2).decode()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                continue
            raise
        for line in text.splitlines():
            urls = [item.strip() for item in line.split(",") if item.strip()]
            if not urls:
                continue
            match = re.search(r"/v(\d{4})\.(\d{2})\.(\d{2})-", urls[0])
            if match is None:
                continue
            day = date(*map(int, match.groups()))
            if start <= day <= end:
                choices.setdefault(day, urls)
    missing = [
        str(start + timedelta(days=n))
        for n in range((end - start).days + 1)
        if start + timedelta(days=n) not in choices
    ]
    if missing:
        raise ValueError("Preferred archive unavailable for " + ", ".join(missing))
    wanted = defaultdict(set)
    dates = {}
    for day, urls in choices.items():
        for url in urls:
            name = urlsplit(url).path.rsplit("/", 1)[-1]
            validate_asset({"date": str(day), "name": name, "url": url, "bytes": 1, "sha256": None})
            wanted[urlsplit(url).path.split("/")[2]].add(url)
            dates[url] = str(day)
    results = {}
    for repository, urls in wanted.items():
        for page in range(1, 41):
            payload = json.loads(
                _download(
                    client,
                    f"https://api.github.com/repos/adsblol/{repository}/releases?per_page=100&page={page}",
                )
            )
            if not isinstance(payload, list):
                raise ValueError("Invalid GitHub release metadata")
            for release in payload:
                for asset in release.get("assets", []):
                    url = asset.get("browser_download_url")
                    if url in urls:
                        digest = asset.get("digest")
                        if digest and not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                            raise ValueError("Invalid published archive checksum")
                        item = {
                            "date": dates[url],
                            "name": asset["name"],
                            "url": url,
                            "bytes": asset["size"],
                            "sha256": digest[7:] if digest else None,
                        }
                        validate_asset(item)
                        results[url] = item
            if urls <= results.keys():
                break
            if len(payload) < 100:
                raise ValueError(
                    "Release metadata missing for " + ", ".join(sorted(urls - results.keys()))
                )
        else:
            raise ValueError("Release pagination exceeded preparation limit")
    return sorted(results.values(), key=lambda item: (item["date"], item["name"]))


def _write(path, data):
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_inputs(root, start, end, *, airport_path=None, filter_path=None, client=None):
    if end < start:
        raise ValueError("End must follow start")
    root = Path(root).expanduser().resolve()
    manifest = root / "manifest.json"
    airports = Path(airport_path) if airport_path else root / "airports.csv"
    selected_filter = Path(filter_path) if filter_path else DEFAULT_FILTER_PATH
    CommercialFilter.model_validate_json(selected_filter.read_text())
    if airport_path and not airports.is_file():
        raise ValueError("Configured airport CSV is missing; configure an existing snapshot")
    with (
        httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": "Flyji-Ingestion/1.0"}
        )
        if client is None
        else nullcontext(client) as http
    ):
        old = (
            json.loads(manifest.read_text())
            if manifest.exists()
            else {"year": start.year, "assets": []}
        )
        for item in old["assets"]:
            validate_asset(item)
        present = {item["date"] for item in old["assets"]}
        needed = {str(start + timedelta(days=n)) for n in range((end - start).days + 1)}
        items = (
            old["assets"]
            if needed <= present
            else [item for item in old["assets"] if not str(start) <= item["date"] <= str(end)]
            + _assets(http, start, end)
        )
        content = None if airports.exists() else _download(http, AIRPORT_URL)
    root.mkdir(parents=True, exist_ok=True)
    if content is not None:
        if shutil.disk_usage(root).free < len(content) + RESERVE_BYTES:
            raise OSError("Preparation must preserve 1 GiB free filesystem space")
        # Validate the candidate before replacing any existing artifact/profile.
        with NamedTemporaryFile(dir=root) as handle:
            handle.write(content)
            handle.flush()
            if not airport_points(Path(handle.name)):
                raise ValueError("Airport snapshot contains no eligible airports")
        _write(airports, content)
    elif not airport_points(airports):
        raise ValueError("Airport snapshot contains no eligible airports")
    manifest_data = {
        "format_version": 1,
        "source": "adsb.lol",
        "year": old["year"],
        "assets": sorted(items, key=lambda item: (item["date"], item["name"])),
    }
    _write(manifest, (json.dumps(manifest_data, indent=2) + "\n").encode())
    return {
        "manifest": str(manifest),
        "airport_catalog": str(airports),
        "filter_path": str(selected_filter.resolve()),
        "data_root": str(root),
    }
