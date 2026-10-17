# Local demo

Install the project once, then start the isolated demo:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/run_demo.py --port 8013
```

Open **http://127.0.0.1:8013/**. The API documentation is at `/docs`. Use a different `--port` if needed; Ctrl+C stops the server.

Type an IATA code directly into **From** or **To**; suggestions are optional. **Via airports** accepts codes separated by commas or spaces, in travel order. Enter `LHR, AMS` (or `lhr ams`) and press Enter to find YYZ → LHR → AMS → CDG. Each airport is required; the list is not a choice between hubs. Suggestions replace the code at the cursor and keep the rest of the list. Duplicate codes and conflicts with endpoints or avoided airports are rejected. Increase **Maximum stops** if needed.

A permitted same-city transfer can satisfy both its arrival and departure airport, in that order: `LHR, LGW, AMS` includes the ground change. Unknown three-letter codes are accepted; missing origin timezone metadata requires UTC input.

Choose a departure date; there is no arrival-deadline input. Routes are searched up to seven days after the departure window. **Last departure date** under **Search filters** extends that window, and connection edits retain the same search horizon.

To steer a selected connection, choose **Change connection**, type `AMS` in **Next airport**, then press Enter. This narrows the destination of the immediately following flight before the option list is calculated. It keeps the selected earlier flights and the full via list. Select a dated flight to apply it; **Undo edit** restores the previous journey. Shared search links and JSON exports also keep the list and its order.

1. Keep **YYZ → CDG**, departing **17 October 2026**. Search the demo schedules.
2. Select **YYZ → LHR → AMS → CDG**. Switch between **Globe** and **Map**, use the camera controls, and try **Show leg**.
3. Scrub **Schedule time**. The map position illustrates scheduled flight progress; the schedule shows both flying and connection time. Switch **Leg times** between airport local and UTC.
4. Clear the optional via field and search again before this airport-change example. At LHR, choose **Change connection**. Select **Any time in the search window**, allow an airport change, then **Find next flights**. Dead-end flights remain visible with an explanation.
5. Apply the **LGW → AMS** flight departing **19 October, 03:00 GMT+1**. The first flight stays selected and the final AMS → CDG leg moves to 19 October. LHR → LGW is shown as a ground change with a demo allowance. Scrubbing that interval shows no invented traveler location.
6. Use **Undo edit** to restore the exact previous flights. Try a not-before date, avoided airport or departure date range. Export JSON to inspect the query, public flight records and licence-notice link; copy the search link to rerun its constraints.

On a phone, **Options**, **Journey** and **Map** switch between tasks. Camera buttons provide an alternative to dragging and scrolling. Flight details and connection editing remain available if the map cannot load.

The twelve schedules, airport metadata and LHR → LGW allowance are synthetic. The allowance is a demo assumption, not a published minimum connection time or travel recommendation. No fares, seat inventory, booking protection or live aircraft positions are supplied.

The script seeds `var/connections-demo.db`; it does not select or reset `var/flights.db`. Both databases and their sidecars are ignored by Git. To inspect the same demo from the terminal:

If an older demo schema exists, it is preserved and the command uses
`var/connections-demo-legs.db`; use the database path printed at startup.

```sh
FLIGHT_DATABASE_PATH="$PWD/var/connections-demo.db" python -m admin search --request var/route-search-request.json --snapshot-out var/search-replay.json
python -m admin debug replay var/search-replay.json
```
