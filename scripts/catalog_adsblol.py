"""Build filtered aircraft-day observations, one raw day at a time, within 20 GiB."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from core.storage import adsblol_budget_lock
from ingestion.adsblol.archive import MAX_BUDGET_BYTES, download_manifest, verified
from ingestion.adsblol.observations import CommercialFilter, summarize_archives
from ingestion.updater import FlightUpdater


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--filter", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("var/adsblol"))
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--date", action="append")
    selection.add_argument(
        "--all-dates",
        action="store_true",
        help="Transfers about 1 TiB for 2025; retained files remain bounded",
    )
    parser.add_argument("--download", action="store_true", help="Fetch missing raw archives")
    parser.add_argument(
        "--discard-raw",
        action="store_true",
        help="Remove verified parts after successful catalogue commit",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    rules = CommercialFilter.model_validate_json(args.filter.read_text())
    canonical = {key: sorted(value) for key, value in rules.model_dump().items()}
    # Version the representation so an older summary without byte offsets gets rebuilt.
    fingerprint = hashlib.sha256(
        ("v2:" + json.dumps(canonical, sort_keys=True)).encode()
    ).hexdigest()
    root = args.data_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / "raw"
    updater = FlightUpdater(root / "observations.db")
    updater.initialize()
    available = {item["date"] for item in manifest["assets"]}
    days = sorted(available if args.all_dates else set(args.date))
    if set(days) - available:
        parser.error("Requested dates absent from manifest")
    for day in days:
        with adsblol_budget_lock(root):
            with updater.connect() as connection:
                done = connection.execute(
                    "SELECT filter_sha256 FROM observation_imports WHERE day=?", (day,)
                ).fetchone()
            if done is not None and done[0] == fingerprint:
                print(f"{day} already catalogued with this filter", flush=True)
                continue
            assets = [item for item in manifest["assets"] if item["date"] == day]
            daily = {
                **manifest,
                "assets": assets,
                "days": 1,
                "bytes": sum(item["bytes"] for item in assets),
            }
            if args.download:
                other_bytes = sum(
                    p.stat().st_size
                    for p in root.rglob("*")
                    if p.is_file() and not p.is_relative_to(output)
                )
                # The SQLite catalogue is capped at 6 GiB; reserve a further GiB
                # for one day's growth and its journal. Existing catalogue is counted above.
                budget = MAX_BUDGET_BYTES - other_bytes - 2**30
                download_manifest(daily, output, budget_bytes=budget)
            paths = [output / day / item["name"] for item in assets]
            if not all(verified(path, item) for path, item in zip(paths, assets, strict=True)):
                parser.error(f"Verified archive parts missing for {day}; use --download")
            observations, stats = summarize_archives(paths, date.fromisoformat(day), rules)
            stats["archive_bytes"] = daily["bytes"]
            updater.save_observation_day(
                date.fromisoformat(day),
                observations,
                filter_sha256=fingerprint,
                stats=stats,
                assets=assets,
            )
            with updater.connect() as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            print(json.dumps({"date": day, **stats}), flush=True)
            if args.discard_raw:
                for path in paths:
                    path.unlink()
                    path.with_name(path.name + ".verified.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
