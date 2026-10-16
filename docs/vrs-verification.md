# VRS route verification

ADSB.lol remains the main historical source. VRS adds an **undated route plausibility
signal**, not actual flight timing or independent proof that a leg operated.
[Upstream](https://github.com/vradarserver/standing-data) is CC0-1.0; the
[ADSB.lol mirror](https://github.com/adsblol/vrs-standing-data) publishes whole CSVs.
Flight.info and Flightradar24 timing remain conditional; [source selection and
historical timing rules](flight-data-sources.md#current-selection-historical-operated-flights-only)
explain why they are not running. Future scheduled ingestion is deferred.

## Producer commands

Finish/adopt the legacy ADSB archive first, following [the handoff workflow](ingestion-review.md).
Do not initialize the planned adoption destination before adoption or run new
producer code against the active unmarked legacy database.

```sh
python -m ingestion --db var/ingestion/ingestion.db vrs import
python -m ingestion --db var/ingestion/ingestion.db vrs report \
  --start 2023-07-01 --end 2023-12-31 --cohort-limit 50 \
  --report var/ingestion/vrs-review.json
```

`import` fetches exactly the published routes and airports CSV collections, each
bounded at 64 MiB in process memory; no raw temporary disk copy or per-flight call.
The measured routes file has 620,700 rows / 18,957,159 bytes. Reuse downloaded files
with paired `--routes-csv PATH --airports-csv PATH`; provide both or neither. Downloads
are explicit; no background polling or guaranteed hourly freshness is implied.

One transaction replaces `ingestion_vrs_routes`, `ingestion_vrs_airports` and
`ingestion_vrs_snapshot`. The receipt stores hashes, byte counts, retrieval time,
URLs, Last-Modified when supplied, licence, row counts and unresolved-mapping counts.
Identical snapshots do not duplicate rows. Missing routes disappear on successful
replacement. Malformed/duplicate rows, cancellation, quota failure or failed fetch
leave the previous snapshot intact. Invalid optional IATA identifiers become unknown
mappings and are counted; they never become invented airport codes. The live CSV
contains two such mappings (`SB0` and `SB2`) in the measured edition.

The ingestion store owns its persisted SQLite/WAL/SHM quota; reference rows count
against it. Import refuses serving/unmarked databases and an active producer job.
Importing references does not write flight facts, review flags, approvals or serving.
Clear/reset also clear all three reference tables, including when over quota.

## Read-only report and review

Reports use a consistent SQLite snapshot and only retained ADSB.lol records in the
requested inclusive UTC departure-day range (or all dates when omitted), excluding
tombstones. They include total/held counts and the largest held airport-pair cohorts.
A bounded cohort list avoids materializing millions of per-flight evidence documents.

| Result | Interpretation |
| --- | --- |
| `exact_two_airport_association` | Callsign's two mapped airports match the directed leg |
| `adjacent_leg_in_multistop_association` | The pair is consecutive within a longer chain |
| `different_undated_route_association` | Fully mapped current association differs; investigate reuse/date changes or endpoint inference |
| `incomplete_airport_mapping` | Reference exists but contains unresolved airport identifiers and does not supply this pair |
| `no_callsign_reference` | No exact callsign association; unknown, not a failed flight |

Normalization trims/uppercases only. Airline-code conversion and zero trimming are
not guessed. Airport mappings use the reference's explicit Code/IATA fields. Route
A–B–C supports A–B and B–C; it never establishes a nonstop A–C. Matching is directed.
These results stay private. A match cannot clear a continuity/timing hold or certify
a flight; disagreement/absence cannot delete or hide an otherwise admitted record.

Admin inspection remains read-only:

```sh
python -m admin --db var/ingestion/ingestion.db inspect query \
  'SELECT receipt_json FROM ingestion_vrs_snapshot'
python -m admin --db var/ingestion/ingestion.db inspect query \
  "SELECT * FROM ingestion_vrs_routes WHERE callsign='ACA541'"
```

Review large held/conflict cohorts first and collect date-specific evidence when
available. Keep unsupported endpoints/times private. Preserve explicit human
approvals. Publication continues to copy only eligible itinerary facts and airport
metadata; reference tables and reports never enter serving SQLite.
