"""Resumable downloads of the preferred full daily ADSB.lol archive copies."""

import hashlib
import json
import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

RESERVE_BYTES = 10 * 2**30
MAX_BUDGET_BYTES = 20 * 2**30


def days_in_year(year):
    current = date(year, 1, 1)
    while current.year == year:
        yield current
        current += timedelta(days=1)


def build_manifest(year, preferred_texts, releases):
    assets = {
        asset["browser_download_url"]: asset for release in releases for asset in release["assets"]
    }
    choices = {}
    for content in preferred_texts:
        for line in content.splitlines():
            urls = [url.strip() for url in line.split(",") if url.strip()]
            if not urls:
                continue
            match = re.search(r"/v(\d{4})\.(\d{2})\.(\d{2})-", urls[0])
            if match is None:
                continue
            day = date(*map(int, match.groups()))
            if day.year != year:
                continue
            items = []
            for url in urls:
                asset = assets.get(url)
                if asset is None:
                    raise ValueError(f"Release metadata missing for {url}")
                name = asset["name"]
                if not re.fullmatch(
                    r"v\d{4}\.\d{2}\.\d{2}-planes-readsb-(prod|staging)-\d+(?:tmp)?\.tar(?:\.[a-z]{2})?",
                    name,
                ):
                    raise ValueError(f"Unexpected archive name: {name}")
                digest = asset.get("digest")
                if digest and not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                    raise ValueError(f"Unexpected digest: {digest}")
                if (
                    urlsplit(url).hostname != "github.com"
                    or f"/adsblol/globe_history_{day.year}" not in url
                ):
                    # A boundary day can be published in the next year's repository.
                    if not url.startswith(
                        f"https://github.com/adsblol/globe_history_{year + 1}/releases/download/"
                    ):
                        raise ValueError(f"Unexpected download URL: {url}")
                items.append(
                    {
                        "date": day.isoformat(),
                        "name": name,
                        "url": url,
                        "bytes": asset["size"],
                        "sha256": digest.split(":", 1)[1] if digest else None,
                    }
                )
            items.sort(key=lambda item: item["name"])
            suffixes = [item["name"].rsplit(".", 1)[1] for item in items]
            if suffixes != ["tar"] and suffixes != [
                "a" + chr(ord("a") + n) for n in range(len(items))
            ]:
                raise ValueError(f"Archive parts missing or out of order for {day}")
            choices[day] = items
    missing = sorted(set(days_in_year(year)) - choices.keys())
    if missing:
        raise ValueError("Missing archive days: " + ", ".join(map(str, missing)))
    items = [item for day in sorted(choices) for item in choices[day]]
    return {
        "format_version": 1,
        "source": "adsb.lol",
        "year": year,
        "selection": "provider preferred daily copies; replica duplicates omitted",
        "days": len(choices),
        "bytes": sum(item["bytes"] for item in items),
        "assets": items,
    }


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


def download_asset(client, item, output):
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
                    if offset + len(chunk) > item["bytes"]:
                        raise ValueError("Download exceeds published asset size")
                    if shutil.disk_usage(output).free < len(chunk) + RESERVE_BYTES:
                        raise OSError(
                            "Download stopped to preserve 10 GiB free; partial file is resumable"
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
    if (
        url.scheme != "https"
        or url.netloc != "github.com"
        or url.query
        or url.fragment
        or not re.fullmatch(
            rf"/adsblol/globe_history_({day.year}|{day.year + 1})/releases/download/[^/]+/{re.escape(name)}",
            url.path,
        )
    ):
        raise ValueError("Unexpected archive URL")
    if type(item["bytes"]) is not int or item["bytes"] <= 0:
        raise ValueError("Asset size must be a positive integer")
    if item["sha256"] is not None and not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
        raise ValueError("Invalid SHA-256")


def download_manifest(manifest, output, client=None, *, budget_bytes=MAX_BUDGET_BYTES):
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
            f"Archive needs {remaining / 2**30:.1f} GiB plus 10 GiB reserve; destination has {free / 2**30:.1f} GiB free"
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
            result = download_asset(client, item, output)
            print(f"{number}/{len(manifest['assets'])} {result}: {item['name']}", flush=True)
    finally:
        if owns:
            client.close()
