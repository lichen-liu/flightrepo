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

`maintenance init` explicitly initializes or upgrades the schema at the selected
path, including parent directories. It leaves existing records intact. Reads,
exports, and `overview` never create a missing file or modify a schema. Flight
management and routing require the Flyji schema; `inspect` works with any SQLite
schema.

## Inspect stored facts

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
All counts include cancelled flights. Fetch timestamps describe the stored
records; they do not establish complete or current provider coverage. Airline
counts include only non-null airline codes.

`flights` is the single flight browser. All filters are optional, and all statuses
are included by default, with or without dates. Use `--status` to select one
status. Results are ordered by departure, provider, and ID. `--start` is inclusive
and `--end` is exclusive; either can be used alone. The default limit is 20 and
the maximum is 1,000. Returned provider/ID pairs identify individual records.

`routes` summarizes direct airport pairs. `search` finds complete journeys with
connections. These commands answer different questions.

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

- `maintenance clear-data --yes` deletes flights, airports, and transfer rules
  transactionally while preserving the SQLite file and schema. It only permits
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
- `ingest` remains the manual AeroDataBox/CSV importer. Provider collection and
  automatic refresh work remain deferred; this cleanup does not change ingestion.

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
