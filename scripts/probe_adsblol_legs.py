"""Check flight-leg candidates against airport metadata; never populate serving DB."""

import argparse
import gzip
import json
from pathlib import Path

from ingestion.adsblol.legs import airport_points, flight_candidates
from ingestion.adsblol.observations import CommercialFilter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "aircraft_json", type=Path, help="Local gzip or plain readsb research sample"
    )
    parser.add_argument("--airports", type=Path, required=True, help="OurAirports airports.csv")
    parser.add_argument("--filter", type=Path, required=True)
    args = parser.parse_args()
    raw = args.aircraft_json.read_bytes()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    rules = CommercialFilter.model_validate_json(args.filter.read_text())
    candidates = flight_candidates(data, airport_points(args.airports), rules)
    print(
        json.dumps(
            {
                "purpose": "research; endpoints and times are inferred, not verified schedules",
                "candidates": [c.model_dump(mode="json") for c in candidates],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
