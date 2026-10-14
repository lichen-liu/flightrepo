"""Extract compact observed itinerary legs one UTC day at a time, then delete raw inputs."""

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path

from ingestion.adsblol.pipeline import extract_range, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--airports", type=Path, required=True, help="OurAirports CSV snapshot")
    parser.add_argument("--filter", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("var/adsblol-itineraries"))
    parser.add_argument(
        "--output-db", type=Path, help="SQLite destination; defaults to DATA_ROOT/itineraries.db"
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--budget-gib", type=float, default=20)
    parser.add_argument(
        "--policy-db", type=Path, help="optional initialized admin DB providing a live quota"
    )
    parser.add_argument("--download", action="store_true", help="download missing daily raw inputs")
    parser.add_argument(
        "--boundary-manifest",
        type=Path,
        help="next year's inventory for January 1 arrival evidence",
    )
    args = parser.parse_args()
    if not math.isfinite(args.budget_gib) or not 0 < args.budget_gib <= 20:
        parser.error("--budget-gib must be greater than zero and at most 20")
    year, _ = inventory(args.manifest, args.boundary_manifest)
    first = args.start or date(year, 1, 1)
    last = args.end or date(year + 1, 1, 1) - timedelta(days=1)
    if args.boundary_manifest and args.end is None:
        last += timedelta(days=1)
    if first > last:
        parser.error("--start must precede --end")
    for receipt in extract_range(
        args.manifest,
        args.airports,
        args.filter,
        args.data_root,
        first,
        last,
        boundary_manifest=args.boundary_manifest,
        limit=int(args.budget_gib * 2**30),
        policy_db=args.policy_db,
        download=args.download,
        departure_year=year,
        output_db=args.output_db,
    ):
        # Keep source receipts in the DB; print compact progress, not all URLs.
        print(
            json.dumps({key: value for key, value in receipt.items() if key != "inputs"}),
            flush=True,
        )


if __name__ == "__main__":
    main()
