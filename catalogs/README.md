# ADSB.lol archive inventory

`adsblol-2025.json` indexes 365 UTC days, with 724 downloadable archive parts.
It contains links, sizes and published checksums, not flight schedules or tracks.
Total preferred volume: 1,035.564 GiB (1.011 TiB).

The inventory uses the provider's [2025 preferred releases](https://github.com/adsblol/globe_history_2025/blob/main/PREFERRED_RELEASES.txt).
December 31 was published in the [2026 repository](https://github.com/adsblol/globe_history_2026/releases/tag/v2025.12.31-planes-readsb-staging-0).
The adjacent-year catalogue also lists December 30; the 2025 preferred selection
is retained for that day. Replica copies are not added together.

Source: [ADSB.lol historical data](https://www.adsb.lol/docs/open-data/historical/),
published under ODbL 1.0. Source archives remain hosted by ADSB.lol on GitHub.
See [the dataset licence](https://github.com/adsblol/globe_history_2025/blob/main/LICENSE-ODbL.txt).

The inventory was checked against public GitHub release asset metadata on
October 14, 2026. Archive URLs or availability can change; every retrieval checks
published length and available SHA-256. This file inventory cannot power itinerary
search: the local database must first contain accepted individual flight legs.

`adsblol-extraction-pilot.json` records the complete December 30 offline extraction
check: 2,138 conservative itinerary-leg candidates in a 2.34 MiB SQLite file,
about 504 bytes per leg including its identity index. All raw daily parts were
removed after commit. This measures storage; it does not establish complete
airline coverage, verified schedules or serving integration.
See the [ingestion reference](../docs/ingestion.md) for sources, licences and usage.
