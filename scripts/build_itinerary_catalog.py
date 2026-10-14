"""Write a normalized itinerary catalogue and gzip flight chunks for HTTP hosting."""

import argparse
import json
from pathlib import Path

from core.connections import Airport, AirportTransfer
from core.models import FlightRecord
from ingestion.catalog import write_itinerary_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flights", type=Path, help="JSON array of normalized FlightRecord objects")
    parser.add_argument(
        "--airports", type=Path, required=True, help="Airport/transfer catalogue JSON"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--base-url", required=True, help="HTTP directory that will host the generated files"
    )
    args = parser.parse_args()
    metadata = json.loads(args.airports.read_text())
    catalog = write_itinerary_catalog(
        [FlightRecord.model_validate(item) for item in json.loads(args.flights.read_text())],
        [Airport.model_validate(item) for item in metadata["airports"]],
        [AirportTransfer.model_validate(item) for item in metadata.get("transfers", [])],
        args.output,
        args.base_url,
    )
    print(
        json.dumps(
            {
                "chunks": len(catalog.chunks),
                "flights": sum(c.flight_count for c in catalog.chunks),
                "catalogue": str(args.output / "catalog.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
