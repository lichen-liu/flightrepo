# Local database administration

Start the shell with `python -m admin` or the installed `flyji` command. Use
`python -m admin --db /absolute/path/to/flights.db` to select a file. Every shell
command also works directly, for example `python -m admin --db var/flights.db overview`.

`help` lists the main commands; `help flight`, `help airports`, `help maintenance`,
`help inspect`, and `help debug` explain each group. `help flight edit` shows
options for an individual action. `quit` exits. Starting the shell does not
initialize a database.

## Select or initialize a database

```text
flyji> db
flyji> db "/path/with spaces/flights.db"
flyji> db var/new-flights.db --create
flyji> overview
```

Selection precedence is a command's `--db` option, the shell's selected file,
`FLIGHT_DATABASE_PATH` / `.env`, then the project's `var/flights.db`. Put `--db`
before the command. Relative paths resolve from the current directory; `~` is
expanded. The shell displays the selected absolute path when it starts.

`db` prints the selected path. `db PATH` verifies an existing SQLite file before
switching; failure keeps the old selection. `db PATH --create` explicitly
initializes the Flyji schema and selects it. A command inside the shell can use
`--db PATH COMMAND` to target another file without changing the shell selection.

`maintenance init` explicitly initializes the schema at the selected
path, including parent directories. It leaves existing records intact. Reads,
exports, and `overview` never create a missing file or modify a schema. Flight
management and routing require the Flyji schema; `inspect` works with any SQLite
schema.

## Inspect stored facts

`storage quota 20GiB` changes the database-owned quota live, including from
another shell. `storage quota` displays it; `storage quota off` disables the
ceiling. `storage status` shows database/WAL/SHM bytes and any excess. Raw archives
and daily staging have a separate working-space budget. Ingestion rechecks policy
on its next storage boundary; lowering it preserves completed facts and pauses
further ingestion when over quota. There is no serving-side download cache.

```text
flyji> overview
flyji> overview --json
flyji> flights --origin YYZ --limit 10
flyji> flights --provider manual --status scheduled
flyji> flights --start 2026-10-17T00:00:00Z --end 2026-10-18T00:00:00Z
flyji> flight show manual FLIGHT_ID
flyji> airports --query YYZ
flyji> routes --start 2026-10-17T00:00:00Z --end 2026-10-18T00:00:00Z
```

`overview` shows counts, directed routes, airlines, catalog coverage, transfer
rules, missing airport metadata, departure/fetch ranges, provider/status counts,
the five busiest direct pairs by stored flight count, and SQLite journal mode.
Stored-fact counts include cancelled and unreviewed candidates; searchable counts
exclude them. Review and searchable totals are shown separately. Fetch timestamps describe the stored
records; they do not establish complete or current provider coverage. Airline
counts include only non-null airline codes.

`flights` is the single flight browser. All filters are optional, and all statuses
are included by default, with or without dates. Use `--status` to select one
status. Results are ordered by departure, provider, and ID. `--start` is inclusive
and `--end` is exclusive; either can be used alone. The default limit is 20 and
the maximum is 1,000. Returned provider/ID pairs identify individual records.

`routes` summarizes direct airport pairs. `search` finds complete journeys with
connections. These commands answer different questions.

## Review uncertain itineraries

```text
flyji> flights --needs-review --provider adsblol --limit 20
flyji> flight show adsblol RECORD_ID
flyji> flight edit adsblol RECORD_ID
flyji> flight approve adsblol RECORD_ID
```

Airport-estimate-only records can power search, continuation planning and calendar
dates with visible estimate labels, even before manual verification. Gaps over
30 minutes, implausible airport timing and other blocking `review_reasons` remain
held unless explicitly reviewed. Callsign changes alone no longer block new
extractions; legacy callsign review flags remain held. Check the airports and time
bounds using the retained evidence;
edit incorrect fields before approving. Approval previews the record and defaults
to no; scripts can supply `--yes`. It preserves inference provenance and marks
`itinerary_reviewed`, protecting the reviewed record from passive reingestion.
Automatic admission never sets `itinerary_reviewed`. You may explicitly review an
already searchable estimate with the same command. An edit alone does not approve
a held record. Website counts distinguish searchable legs from held candidates.

## Create, edit, and delete flights

```text
flyji> create-route
flyji> flight edit manual FLIGHT_ID
flyji> flight delete manual FLIGHT_ID
```

`create-route` enters a connected sequence of flight facts. Each leg has a
destination, flight number, and scheduled departure/arrival with `Z` or an offset.
Each next leg starts at the previous destination and cannot depart before the
previous arrival. The full sequence is previewed in UTC before all records are
saved in one transaction. IDs are generated under provider `manual`; use `flights`
to see them. This command does not store a named journey; search connection rules
still apply to these flights.

`flight edit` keeps a field when you press Enter. `-` clears an optional airline,
actual timestamp, or codeshare list. It validates and previews the replacement
before saving. Identity and fetch time are preserved in interactive editing.
For scripts, `flight edit PROVIDER ID --file FILE` replaces an existing flight
from a single complete FlightRecord JSON object, with no prompts. Its identity
must match. Both modes refuse to overwrite a record changed during review.

`flight delete` previews and confirms deletion of one flight; scripts can use
`--yes` to skip prompts. It also rejects a flight changed during review. Type
`cancel`, press Ctrl+C, or send EOF during a wizard to discard pending changes.
Save/delete prompts default to no. Cancelling route creation does not create a
new database file.

## Create an airport

Run `create-airport` in the shell, or target a database directly:

```sh
python -m admin --db var/flights.db create-airport
```

Enter the three-letter IATA code, airport name, city ID, IANA timezone (such as
`America/Toronto`), latitude and longitude in decimal degrees. Codes are converted
to uppercase. Reuse an existing city ID to group airports in the same city;
transfer allowances are still configured separately through the catalog import.

The command validates and previews the airport before saving. On invalid input,
correct the fields or press Enter to keep an entered value. Save defaults to no;
`cancel`, Ctrl+C and EOF discard the entry without creating a database. Confirming
creates the Flyji schema if needed and saves the airport in the selected file.
An existing code is refused, including when another writer inserts it during the
prompts. Use `airports import` to update existing metadata. The airport is then
available to `airports` and the server catalog, even without stored flights.

## Batch and catalog files

```text
flyji> import-json var/flights.json
flyji> export-json var/flights-export.json
flyji> airports import var/airports.json
flyji> airports export var/airport-catalog.json
```

Flight batch files are arrays of FlightRecord objects. Imports upsert matching
provider/ID records and retain omitted records. Catalog files contain `airports`
and optional directed `transfers`, including names, timezones, coordinates and
same-city transfer allowances. Imports validate the complete batch before writing.
Keep working files under `var/`.

## Ingest in separate sessions

`ingest sources` explains the three pipelines and shows configured state. Source
names are explicit; flags for one source cannot be used with another. Bare
`ingest` in the interactive shell starts guided ingestion. Every command also
works through `python -m admin --db FILE` or the installed `flyji` command.

| Command | Purpose |
| --- | --- |
| `ingest adsblol prepare` | Fetch preferred archive metadata and an immutable airport CSV; no raw flight downloads |
| `ingest adsblol configure` | Save existing manifest, airport CSV, optional quality policy and working-directory paths |
| `ingest adsblol run` | Extract historical observed legs one UTC day at a time |
| `ingest adsblol status` | Completed periods, accepted legs, current batch and rejection diagnostics |
| `ingest csv configure/run/status` | Import a licensed CSV with schedules and optional actual times/type |
| `ingest aerodatabox configure/run/status` | Fetch airport schedules/status using configured API credentials |
| `import-json FILE` | Import already normalized FlightRecord JSON |
| `airports import FILE` | Import airport and directed-transfer metadata |

```text
flyji> ingest sources
flyji> ingest adsblol prepare --start 2023-07-01 --end 2023-12-31 --finish-year
flyji> ingest adsblol run --start 2023-07-01 --end 2023-07-03
flyji> ingest adsblol run --start 2023-08-10 --end 2023-08-12
flyji> ingest adsblol status
flyji> storage quota 20GiB
flyji> storage status
```

Preparation saves generated paths in the selected database and bundles the
quality defaults in the installed package, without type/callsign whitelists. It reuses an existing airport CSV and
optional `--policy` file unchanged. Missing archive periods fail without configuring the
pipeline. `configure` is useful when you already have inputs. Output defaults to
the selected admin database; `--output-db` is an explicit override. `--data-root`
is working space, not a second serving database.

ADSB jobs may overlap, have gaps or run out of order. Every day commits separately;
completed days skip downloads and stable keys deduplicate repeats. Default guided
jobs suggest three days; explicit ranges can be longer, including a year. Ctrl-C
preserves prior commits. `--offline` processes only verified local raw inputs.
`--refresh-diagnostics` deliberately reprocesses selected days. `--departure-year`
limits retained departures to that UTC year. For a range ending December 31,
`--finish-year` processes January 1 arrival evidence and excludes next-year
departures; prepare that boundary input first. `--boundary-manifest` also accepts
an existing adjacent-year manifest. Keep the airport snapshot/quality policy unchanged
for an existing dataset.

The live `storage quota` covers database/WAL/SHM only; `--budget-gib` independently
limits raw/staging working space. JSON stderr events and saved receipts distinguish
committed facts from provisional progress, rejected segments and invalid samples.
These counts do not establish complete airline coverage. See the compact
[ingestion reference](ingestion.md) for source licences, extraction and statistics.

CSV/API jobs use explicit airport codes and an inclusive date range, limited to
`FLIGHT_MAX_QUERY_DAYS` (31 by default). Both upsert source identities and protect
local edits/deletes. AeroDataBox needs `FLIGHT_PROVIDER_API_KEY`; its adapter does
not currently extract aircraft type. CSV can provide `aircraft_type_code`.
There is no serving-side network ingestion.

## Search journeys

`search` without input flags launches guided prompts. It accepts ordered via
and avoided airports, leg/connection bounds, ranking, an arrival deadline, and
optional airport changes. The equivalent direct command is:

```sh
python -m admin --db var/flights.db search \
  --origin YYZ --destination CDG --via LHR AMS --avoid JFK \
  --start 2026-10-17T00:00:00Z --end 2026-10-18T00:00:00Z \
  --horizon 2026-10-19T00:00:00Z --max-legs 3 \
  --min-connection 45 --max-connection 360 --ranking earliest_arrival
```

Use `search --request FILE` for a full RouteSearchRequest JSON, including custom
engine budgets. A request file cannot be mixed with search input flags. Add
`--snapshot-out FILE` to any input mode to capture facts, request, and result.
`--trace` adds developer diagnostic events when using flags; request files can
also enable tracing. Guided search uses the engine defaults for tracing/budgets.

Rankings are `fewest_legs`, `shortest_elapsed`, and `earliest_arrival`. Departure
end is exclusive and the arrival deadline is inclusive. Airport changes require
catalogued same-city transfer rules. Results consistently contain `query`,
`items`, `diagnostics`, and airport metadata. Check `diagnostics.exhaustive` and
termination reasons when budgets stop a search. Search never fetches or saves
flight facts.

## Secondary tools

- `maintenance apply-admission` applies labeled-estimate admission to an existing
  database, refreshes the search view and flags implausible ADSB airport/time
  claims using retained fields. It is safe to rerun and protects concurrent admin
  edits. It preserves IDs, ingestion receipts, quota and manual decisions; it
  neither downloads raw archives nor marks human approvals. Use a compatible
  backend/frontend with estimate labels before serving the updated database.
- `maintenance clear-data --yes` deletes flights, airports, transfer rules, and
  any extracted observed facts/configuration/receipts/boundary state
  transactionally while preserving the SQLite file, schema and ingestion profiles. It only permits
  databases under this installation's `var/`. Stop ingestion if it must stay empty.
- `inspect tables` lists table definitions. `inspect query "SELECT ..." --limit 50`
  returns columns, row arrays, and a truncation indicator. Only a single read-only
  SELECT is allowed, including CTEs; writes, PRAGMA, database attachment, and
  extension loading are rejected. The row limit bounds output, not query work.
- `debug continue-route REQUEST.json` calculates the remaining journey after a
  retained prefix using a RebuildRequest; it saves no flight records. It supports
  `--snapshot-out FILE`.
- `debug replay SNAPSHOT.json` repeats a captured routing calculation and checks
  whether the result matches. It opens no database and never restores records.
  This is a developer tool for reproducing unexpected routing decisions.

See the [routing engine guide](engine/routing.md) for request shapes and limits.

## Command changes

Duplicate entry points have been removed. Update existing scripts as follows:

| Previous command | Replacement |
|---|---|
| `shell` | Run `python -m admin` with no command |
| `stats` | `overview --json`; initialization is `maintenance init` |
| `list-flights` | `flights` |
| `show PROVIDER ID` | `flight show PROVIDER ID` |
| `edit PROVIDER ID` | `flight edit PROVIDER ID` |
| `update PROVIDER ID FILE` | `flight edit PROVIDER ID --file FILE` |
| `delete PROVIDER ID` | `flight delete PROVIDER ID` |
| `reset --yes` | `maintenance clear-data --yes` |
| `import-airports FILE` | `airports import FILE` |
| `export-airports FILE` | `airports export FILE` |
| `search-route` | `search` with no input flags |
| `search-itineraries FILE` | `search --request FILE` |
| `itineraries ...` | `search ... --horizon TIME`; results include diagnostics |
| `rebuild FILE` | `debug continue-route FILE` |
| `replay-routing FILE` | `debug replay FILE` |
| `tables` / `query SQL` | `inspect tables` / `inspect query SQL` |

Unlike the former dated `flights` command, the unified browser includes cancelled
flights by default. Use `--status scheduled` when you want only scheduled records.
HTTP API contracts and the website's routing engine remain unchanged.

## Shared serving schema

New files use the `flight-legs` contract. `overview --json` exposes the schema version;
`overview` separates scheduled, actual and observed searchable counts and shows
successful/failed/running ingestion attempts. ADSB ingestion defaults to the
selected database. `--data-root` selects disposable working storage; an explicit
`--output-db` overrides the serving target. The final job summary prints both paths.

`flight show`, `flights`, search, export, edit and delete operate on accepted
observed legs too. Edits retain four time bounds, callsign and aircraft type;
reprocessing preserves local edits and deletion markers. `import-json` explicitly
restores an identity. Search/rebuild request JSON can set `time_basis` to
`scheduled`, `actual` or `observed`. Unknown airport timezones require UTC input.
A placeholder airport created by ingestion can be completed with `airport create`
or catalog import; directed transfers still require known city membership.

A schema change requires a fresh initialized file; opening admin never silently
converts an old database or seeds synthetic flights. See [the schema reference](database-schema.md).
