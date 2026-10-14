# Ingestion pipeline

Updated October 14, 2026. Process one archive day, keep compact **observed
itinerary-leg candidates** with aircraft type, then delete raw inputs. The
December 30 pilot is complete; shared serving is implemented, and full-year
extraction remains unfinished.

## Sources and licences

| Source | Input and purpose | Data licence |
| --- | --- | --- |
| [ADSB.lol historical releases](https://www.adsb.lol/docs/open-data/historical/) | Daily aircraft observations and type codes, hosted in [2025 GitHub releases](https://github.com/adsblol/globe_history_2025/releases) | [ODbL 1.0](https://github.com/adsblol/globe_history_2025/blob/main/LICENSE-ODbL.txt) |
| [OurAirports](https://ourairports.com/data/) | [airports.csv](https://davidmegginson.github.io/ourairports-data/airports.csv): airport codes, coordinates, type and scheduled-service flag for endpoint matching | Public domain, as stated on its downloads page |

Treat the extracted ADSB dataset as an ODbL derivative. Preserve attribution and
licence links. Public distribution/serving must follow its notice, share-alike
and machine-readable database/alteration-access conditions (§4.2–4.6). Internal
use has the §4.5(c) exception; the database licence does not cover application
code (§2.3(a)). See the linked licence for the exact conditions.

Attribution: “Contains information from ADSB.lol, available under the Open
Database Licence (ODbL) 1.0.” Link both. OurAirports credit is optional.
`extraction_sources` embeds source/licence URLs in the database; rerunning the
extractor adds these notices to existing outputs without downloading again.

## Raw shape

[`catalogs/adsblol-2025.json`](../catalogs/adsblol-2025.json) maps UTC days to
preferred archive-part URLs, lengths and available SHA-256 values. It covers
365 days and 724 parts; December 31 is hosted in the adjacent-year repository.
These URLs download raw archives, not individual itinerary records.

Parts form a tar stream. Aircraft members are gzip-compressed JSON under
`traces/xx/trace_full_<icao>.json`; heatmaps and other members are skipped.
The [readsb trace format](https://github.com/wiedehopf/readsb/blob/dev/README-json.md#trace-jsons)
looks like this illustrative example:

```json
{
  "icao": "a5ea5c", "t": "A320", "dbFlags": 0, "timestamp": 1735689600,
  "trace": [
    [0, 40, -70, "ground", 0, 0, 0, 0, {"flight": "UAL123"}],
    [10, 40, -70, 500, 150, 0, 0, 0, null]
  ]
}
```

`timestamp` is Unix seconds; row `[0]` is its seconds offset. Rows `[1:3]` are
latitude/longitude, `[3]` is altitude or `"ground"`/null, `[6]` contains flags
(bit 1 means stale position), and `[8].flight` supplies a callsign when present.
Top-level `t` is aircraft type; `dbFlags & 1` identifies military metadata.
Callsigns are radio identifiers, not verified marketing flight numbers.

## Extraction and retention

```text
daily URL inventory → download/verify → read one aircraft member
                   → filter/reconstruct in staging → atomic SQLite commit → delete raw day
```

1. Download one preferred copy; resume partials, verify length/available checksum and
   record URLs/hashes. Read members in memory rather than extracting to disk.
2. Apply [`adsblol-commercial-filter.json`](../fixtures/adsblol-commercial-filter.json):
   listed fixed-wing types, no military flag, and one consistent commercial
   callsign per accepted movement. The prefix list includes cargo operators.
3. Require ground observations at both endpoints, an unambiguous airport within
   5 km, transition brackets/gaps at most 120 seconds, and at least 300 seconds
   between first and last airborne observations. Reject stale/missing positions,
   changing callsigns, unknown types and ambiguous endpoints.
4. Save identity, airport pair, callsign, type, departure/arrival bounds and
   provenance. Retain compact per-day airport/time/callsign boundary summaries
   for later adjacent backfills; discard positions, speeds and tracks.
5. Atomically commit legs, state and receipt; delete verified raw parts. Failed
   days roll back and keep inputs. Restarts skip committed days and finish cleanup.

The shared output stores typed `flight_legs`, sources, ingestion attempts, receipts
and compact boundary state. Compatibility `flights`/`itinerary_legs` views read
those same facts; the API and admin see committed days immediately. Stable source
keys prevent duplicates. Only two observed-time query indexes are populated for
an observed-only dataset. Multi-leg journeys are assembled at query time.

Observed bounds are not scheduled/gate times. Search uses explicit observed mode,
conservative interval connections and visible uncertainty. No flight number or
airport timezone is invented. See [the shared schema](database-schema.md).

## Run and resume

Download the airport snapshot once and reuse it unchanged for the run:

```sh
mkdir -p var/adsblol-2025
curl -fL https://davidmegginson.github.io/ourairports-data/airports.csv \
  -o var/adsblol-2025/airports.csv
PYTHONPATH=src python scripts/extract_adsblol_itineraries.py catalogs/adsblol-2025.json \
  --airports var/adsblol-2025/airports.csv --filter fixtures/adsblol-commercial-filter.json \
  --data-root var/adsblol-2025 --download
```

`--start/--end YYYY-MM-DD` selects one job's range. Rerun, overlap, skip periods
or backfill earlier dates in the same root; completed days skip downloads and
stable keys prevent duplicate legs. Adjacent periods reconnect overnight evidence
from summaries, including when arrival days were ingested first. Changed
filters/coordinates require a new data root. Older outputs refresh missing
boundary summaries when a day is requested again with `--download`.
`--boundary-manifest` supplies next-year inputs for January 1 arrivals;
otherwise the final receipt reports unresolved airborne state.

The [admin shell](admin.md#ingest-in-separate-sessions) wraps this same pipeline:
`ingest configure --source adsblol` saves paths once, then `ingest` prompts for
dates or accepts `--start/--end`. Default suggested jobs span three days, with a
31-day default maximum. `ingest status` reports coverage. Each day commits
independently; cancellation keeps prior days and a later invocation resumes.

Use `--output-db var/hosted/<name>.db` in either entry point for an exact SQLite
destination; `--data-root` remains its disposable download/working directory.
The default is `DATA_ROOT/itineraries.db`. The admin defaults to the selected serving file; an explicit output override
can select another database. Observed bounds retain their meaning. Quota accounting
includes output and SQLite sidecars once, even when it is also the policy file.
`maintenance clear-data --yes` clears observed records and resume state too;
confirm the destination and stop ingestion before clearing a real database.

## Observability

Daily receipts persist versioned aggregate diagnostics after raw cleanup;
`ingest status` totals the latest successful receipt for each day and shows the
latest report. Reasons distinguish excluded aircraft (type/military/identity),
ignored samples (stale/invalid/outside-day/duplicate), interrupted airborne
fragments (coverage gaps, invalid samples), and rejected landing candidates
(missing/ambiguous endpoints, short duration, missing/changing/unselected
callsign). Idle ground samples are not counted as rejected flights. Previous/next
day deferrals and boundary joins/rejections have separate counters.

These units are **aircraft members, sample rows and observed fragments/events**,
not verified flights or loss percentages. A single real flight can produce
multiple fragments. Deferrals are historical snapshots and can resolve later;
accepted candidates can still be outside the selected departure-year scope.
No raw position payloads are retained for diagnostics.

Entry points emit JSON events on stderr: `day_started`, `day_progress` every
1,000 members, `day_finished`, and `day_failed`. Progress is provisional;
failed days report `committed`, `records_applied`, phase and available aircraft/
row context. Rollbacks apply zero records even if provisional insertion counters
were positive; checkpoint/cleanup failures can occur after a successful commit.
Failed diagnostics are in logs, not a successful receipt. For a retained log:
`python -m admin --db FILE ingest ... 2>> var/ingestion.log`.

Old receipts without diagnostics remain unknown. `--refresh-diagnostics`
explicitly reprocesses selected days using stable keys; the offline script also
needs `--download` if raw inputs were removed. Normal repeats skip processing and
label logged receipt counts as historical. Coverage still needs comparison with
a trusted flight list; these diagnostics cannot prove lossless extraction.

The **20 GiB** cap includes output, raw/partial files and SQLite sidecars.
`--budget-gib` lowers it; `--policy-db /path/flights.db` reads the initialized
admin DB's live `cache quota` and includes its storage, capped by the CLI limit.
Insufficient space stops extraction without deleting legs. Allow 4 GiB for raw
inputs and 1 GiB working space. [LRU](itinerary-cache.md) applies to recoverable
downloads, not the only copy of extracted legs.
Downloads also preserve 1 GiB of free filesystem space.

## Measured statistics

The 2025 inventory totals **1,035.56 GiB (1.01 TiB)** of transfer, averaging
2.84 GiB/day (range 1.57–3.58). Daily processing avoids retaining that full volume.
The [December 30 pilot report](../catalogs/adsblol-extraction-pilot.json) records:

| Stage | Record count/unit | Size |
| --- | ---: | ---: |
| Raw archive | 61,215 aircraft-day members | 2.98 GiB |
| Commercial fixed-wing subset, earlier filter probe | 13,241 aircraft-days | 0.97 GiB compressed traces; not retained |
| Complete inferred legs | 2,138 legs, 39 aircraft types | 1.03 MiB including identity index; **504 bytes/leg** |
| Entire pilot output, before per-day boundary summaries | Legs, config, receipts, source notices and current carry state | 2.34 MiB; zero raw archive parts left |

The earlier subset contained 36.8 million position samples. Aircraft-days are
not flight legs. This partial pilot does not establish full airline coverage.

At 504 bytes/leg, 20 GiB holds about 42.6 million legs; a 15 GiB facts allowance
holds 32 million while reserving 5 GiB for processing. Capacity scenarios:

| Retained legs/day | 20 GiB data only | 15 GiB data + 5 GiB working |
| ---: | ---: | ---: |
| 25,000 | 4.7 years | 3.5 years |
| 50,000 | 2.3 years | 1.8 years |
| 100,000 | 1.2 years | 10.5 months |

These extrapolations exclude growth in index/metadata overhead, including the
new per-day boundary summaries needed for out-of-order backfills. Raw transfer
cost and extraction accuracy are separate from final storage size.

## Other ingestion paths

[CSV/JSON/AeroDataBox imports](admin.md) and optional
[catalogue downloads](itinerary-cache.md) supply scheduled `FlightRecord` data.
Licences follow each feed/account. These paths do not convert ADSB observations
into schedules.
