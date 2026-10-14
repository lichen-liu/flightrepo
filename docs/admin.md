# Local database administration

The admin shell manages local SQLite files. Choose the database when starting:

```sh
python -m admin --db /absolute/path/to/flights.db
# Equivalent installed entry point:
flyji --db /absolute/path/to/flights.db
```

Database selection precedence is `--db`, then the shell's selected file, then
`FLIGHT_DATABASE_PATH` / `.env`, then the project's `var/flights.db`. Put `--db`
before the command. Relative paths resolve from your current directory; `~` is
expanded. The shell displays the selected absolute path when it starts.

```text
flyji> db
flyji> db "/path/with spaces/flights.db"
flyji> db var/new-flights.db --create
flyji> overview
```

`db PATH` verifies that an existing SQLite file can be opened before switching.
A failed switch keeps the old selection. `--create` explicitly initializes the
Flyji schema, including parent directories. `stats` also initializes it for
compatibility. Reads and exports do not create files or modify schemas.
`--db PATH COMMAND` inside the shell selects another file for that command only.

`tables` and `query` can inspect any SQLite database. Flight management,
`overview`, and routing need the Flyji schema; run `stats` once to initialize or
upgrade a Flyji database. General schema migrations and arbitrary SQL writes
are outside these tools.

## Overview and inspection

```text
flyji> overview
flyji> overview --json
flyji> tables
flyji> list-flights --origin YYZ --limit 10
flyji> list-flights --provider manual --status scheduled
flyji> show manual FLIGHT_ID
flyji> query "SELECT origin_iata, destination_iata, COUNT(*) AS flights FROM flights GROUP BY origin_iata, destination_iata" --limit 50
```

`overview` shows flights, directed routes, airlines, airports used in flights,
airport catalog size, transfer rules, missing airport metadata, schedule range,
fetch range, provider/status counts, and the top five routes by stored flight
count. All overview counts include cancelled flights. An airline count uses
non-null `airline_iata` values; manually entered flights without an airline code
are not counted as airlines. Fetch timestamps describe imported facts, rather
than proving current provider coverage.

`list-flights` includes cancelled flights and sorts by departure and identity.
It accepts optional origin, destination, provider, and status filters without
requiring dates. For a date window use the existing `flights` command; it excludes
cancelled flights unless `--include-cancelled` is supplied. Both commands return
`provider` and `provider_id`, which identify records for subsequent operations.

`query` accepts one quoted SQL SELECT, including CTEs. Results contain `columns`,
row arrays, and `truncated`; duplicate column names remain distinct. The default
limit is 20 rows, with a maximum of 1,000. It opens SQLite read-only and rejects
writes, PRAGMA commands, database attachment, and extension loading. The result
limit bounds returned rows, rather than the work required by a complex query.

## Create, update, and delete

```text
flyji> create-route
flyji> edit manual FLIGHT_ID
flyji> delete manual FLIGHT_ID
```

`create-route` prompts for the origin, then each leg's destination, flight number,
and scheduled departure/arrival. Timestamps require `Z` or an offset. Each next
leg starts at the previous destination and cannot depart before the previous
arrival. The full route is previewed in UTC before all flight records are saved
in one transaction. IDs are generated under provider `manual`; view them with
`list-flights`. These are flight facts, not stored named journeys. Routing still
applies connection constraints to them.

`edit` shows the original record and prompts for its flight number, airline,
airports, scheduled/actual times, status, and comma-separated codeshares. Enter
keeps the displayed value; `-` clears airline, actual times, or codeshares. The
provider, ID, and fetch timestamp are preserved. Invalid input cannot be saved;
the wizard lets you re-enter the fields. Review the replacement before saving.

`delete` previews the selected record and asks for confirmation. Scripts can use
`delete PROVIDER ID --yes` to skip the prompt. Both edits and deletes refuse to
write if the record changed or disappeared while being reviewed.

Type `cancel`, press Ctrl+C, or send EOF during a wizard to discard its pending
changes. Save/delete prompts default to no. Cancelling route creation does not
create a new database file.

For scripted updates, save a single complete `FlightRecord` JSON object and run:

```sh
python -m admin --db var/flights.db update manual FLIGHT_ID var/edited-flight.json
```

`update` validates the object, requires an existing matching provider/ID, and
replaces its other fields. To create or upsert batches, continue using
`import-json`; `export-json`, `import-airports`, and `export-airports` remain
available. `reset --yes` clears the database only under the project's `var/`.

## Routing queries

Use `search-route` for guided prompts: origin, destination, departure window,
arrival deadline, optional via/avoided airports, maximum legs, connection bounds,
ranking, result limit, airport changes, and tracing. This command queries the
stored flights without fetching or saving new ones.

The equivalent command with flags is:

```sh
python -m admin --db var/flights.db search \
  --origin YYZ --destination CDG --via YUL --avoid JFK \
  --start 2026-10-17T00:00:00Z --end 2026-10-18T00:00:00Z \
  --horizon 2026-10-19T00:00:00Z --max-legs 3 \
  --min-connection 45 --max-connection 360 \
  --ranking earliest_arrival --trace --snapshot-out var/search-replay.json
```

Supported rankings are `fewest_legs`, `shortest_elapsed`, and `earliest_arrival`.
Use `--allow-airport-change` to permit catalogued same-city transfer rules.
Results include diagnostics: check `exhaustive` and termination reasons when a
search stops at a budget limit. Departure end is exclusive; the arrival deadline
is inclusive. `search-route` also accepts `--snapshot-out`.

`search-itineraries REQUEST.json` supports the full typed request, including
custom engine budgets. `rebuild REQUEST.json` recalculates a journey with a
retained prefix. Both support snapshots; `replay-routing SNAPSHOT.json`
recalculates offline and reports whether the result matches. See the
[routing engine guide](engine/routing.md) for request shapes and limitations.

Provider data collection and automatic refresh are deferred. This workflow
creates, inspects, edits, and queries the local facts you already have.
