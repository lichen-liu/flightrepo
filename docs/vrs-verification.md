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
with paired `--routes-csv PATH --airports-csv PATH`; provide both or neither. The producer publication command also retrieves these CSVs once when absent
(`--vrs auto`); `cached`, `refresh` and `skip` control reuse. No background polling
or guaranteed hourly freshness is implied.

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
The report remains read-only. Producer `vrs audit --apply` (also available as `admit --apply`) and daily
publication apply the user-selected best-effort policy below. Importing or
reporting alone never changes admission.

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

## Explicit best-effort route admission

```sh
python -m ingestion --db var/ingestion/ingestion.db vrs admit \
  --start 2023-07-01 --end 2023-12-31 --report var/ingestion/vrs-admission-preview.json
python -m ingestion --db var/ingestion/ingestion.db vrs admit --apply \
  --start 2023-07-01 --end 2023-12-31 --report var/ingestion/vrs-admission-applied.json
```

This opt-in policy confirms exact directed two-airport matches and consecutive
pairs in longer chains. It waives only the observation-gap admission block and
replaces the unverified-airport badge with “VRS route match.” It does
not assert historical validity, exact timing or continuous observation. Observed
time windows, original reasons, source identity and aircraft type remain unchanged.
Timing conflicts and any other blocking reason still hold the record. Reversed,
nonconsecutive, missing or conflicting pairs do not qualify. No fuzzy airport match.

An optional typed `route_verification` field stores the reference hash, callsign,
airport pair, mapped reference chain and audit result: exact, adjacent, different,
incomplete or missing. Negative results never count as route verification. SQL, routing, calendar, admin, audit and publication use
the same policy. Changed callsigns/endpoints invalidate old evidence. The private
store retains an immutable snapshot receipt in `ingestion_documents`; only compact
per-flight evidence and VRS attribution enter serving, never the reference catalog.
An explicit writer performs an additive field/view upgrade for existing stores.
Old readers that only understand positive matches must be upgraded before
negative results are published. [Unified confidence labels and review](itinerary-quality.md)
explain how all outcomes appear to consumers.

Application is resumable/idempotent, one transaction per UTC departure day, with a
job lock, live DB-only quota checks and progress counts. It protects tombstones,
manual edits and human approvals. Refreshing a reference alone leaves published
evidence intact; the next publication or explicit audit reconciles it, revoking automatic
confirmation on mismatch. Subsequent publication retracts newly held owned facts.
Human review is never bulk marked. Admin remains inspection/reset/clear only for
private ingestion. Publication/deployment remain explicit operations; serving requests never run audits.
