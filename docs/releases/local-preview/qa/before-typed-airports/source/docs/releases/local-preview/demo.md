# Local demo

Install the project once, then start the isolated demo:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/run_demo.py --port 8013
```

Open **http://127.0.0.1:8013/**. The API documentation is at `/docs`. Use a different `--port` if needed; Ctrl+C stops the server.

1. Keep **YYZ → CDG**, departing **17 October 2026**. The initial search returns four journeys from the demo schedules.
2. Select **YYZ → LHR → AMS → CDG** with the 12h 30m duration. Switch between **Globe** and **Map**, use the camera controls, and try **Show leg**.
3. Scrub **Schedule time**. The map position illustrates scheduled flight progress; the schedule shows both flying and connection time. Switch **Leg times** between airport local and UTC.
4. At LHR, choose **Change connection**. Select **Any later time**, allow an airport change, then **Find next flights**. Dead-end flights remain visible with an explanation.
5. Apply the **LGW → AMS** flight departing **19 October, 03:00 GMT+1**. The first flight stays selected and the final AMS → CDG leg moves to 19 October. LHR → LGW is shown as a ground change with a demo allowance. Scrubbing that interval shows no invented traveler location.
6. Use **Undo edit** to restore the exact previous flights. Try a not-before date, avoided airport or arrival deadline. Export JSON to inspect the query, selected records and snapshot evidence; copy the search link to rerun its constraints.

On a phone, **Options**, **Journey** and **Map** switch between tasks. Camera buttons provide an alternative to dragging and scrolling. Flight details and connection editing remain available if the map cannot load.

The twelve schedules, airport metadata and LHR → LGW allowance are synthetic. The allowance is a demo assumption, not a published minimum connection time or travel recommendation. No fares, seat inventory, booking protection or live aircraft positions are supplied.

The script seeds `var/connections-demo.db`; it does not select or reset `var/flights.db`. Both databases and their sidecars are ignored by Git. To inspect the same demo from the terminal:

```sh
FLIGHT_DATABASE_PATH="$PWD/var/connections-demo.db" python -m admin search-itineraries var/route-search-request.json --snapshot-out var/search-replay.json
python -m admin replay-routing var/search-replay.json
```

The [desktop capture](qa/browser/desktop-initial.png), [edited journey](qa/browser/desktop-overnight.png) and [phone capture](qa/browser/touch-phone-map.png) show the audited build. These are captured interfaces, not design mockups.
