"""Research one ADSB.lol archive day; outputs are not searchable flight records."""

import argparse
import json
from datetime import date
from pathlib import Path

from ingestion.adsblol.archive import MAX_BUDGET_BYTES, download_manifest, validate_asset, verified
from ingestion.adsblol.observations import CommercialFilter, summarize_archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--filter", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("var/adsblol"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--download", action="store_true", help="Fetch missing raw archives")
    parser.add_argument(
        "--discard-raw",
        action="store_true",
        help="Remove verified parts after writing research output",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    rules = CommercialFilter.model_validate_json(args.filter.read_text())
    root = args.data_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / "raw"
    assets = [item for item in manifest["assets"] if item["date"] == args.date]
    if not assets:
        parser.error("Requested date absent from manifest")
    day = date.fromisoformat(args.date)
    for item in assets:
        validate_asset(item)
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
        download_manifest(daily, output, budget_bytes=MAX_BUDGET_BYTES - other_bytes - 2**30)
    paths = [output / args.date / item["name"] for item in assets]
    if not all(verified(path, item) for path, item in zip(paths, assets, strict=True)):
        parser.error("Verified archive parts missing; use --download")
    summaries, stats = summarize_archives(paths, day, rules)
    stats["archive_bytes"] = daily["bytes"]
    target = root / f"pilot-{args.date}.jsonl"
    content = "".join(s.model_dump_json() + "\n" for s in summaries)
    used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if used + len(content.encode()) > MAX_BUDGET_BYTES:
        parser.error("Research output exceeds combined 20 GiB allowance")
    target.write_text(content)
    print(
        json.dumps(
            {
                "date": args.date,
                **stats,
                "note": "Aircraft-day research summaries; no flight legs or routes ingested.",
            }
        )
    )
    if args.discard_raw:
        for path in paths:
            path.unlink()
            path.with_name(path.name + ".verified.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
