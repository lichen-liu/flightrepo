# Run Flyji through Cloudflare

From the repository root, install the app and connector:

```sh
python3 installation/install.py
brew install cloudflared
```

Use a current `cloudflared`; the private token-file option requires version
2025.4.0 or later. [Downloads](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/),
[token-file support](https://developers.cloudflare.com/tunnel/reference/run-parameters/).

1. In Cloudflare's dashboard, open **Networking → Tunnels** and create a separate,
   remotely managed tunnel named **flyji**. Copy only its tunnel token, then save
   it through the hidden terminal prompt:

   ```sh
   ./deployment/flyji tunnel
   ```

   The token stays in the private runtime, outside Git and command arguments.
   Saving it does not start the connector.

2. Start the installed app and connector, then check local operation:

   ```sh
   ./deployment/flyji start
   ./deployment/flyji status
   ./deployment/flyji check
   ```

3. In the tunnel's **Routes**, add a **Published application** route:

   | Setting | Value |
   |---|---|
   | Hostname | `fly.boboji.fyi` |
   | Service URL | `http://127.0.0.1:8014` |
   | HTTP Host Header | `fly.boboji.fyi` |

   Set the Host header under **Additional application settings → HTTP**. The
   dashboard configures the hostname's DNS route; the header must agree with
   Flyji's hostname validation.
   [Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/),
   [origin parameters](https://developers.cloudflare.com/tunnel/reference/origin-parameters/).

4. Check the actual public path and open **https://fly.boboji.fyi**:

   ```sh
   ./deployment/flyji check --public
   ```

   A running connector or passing local check alone does not establish that the
   hostname works. Confirm it from another device or network as well.

The application path is:

```text
Browser → Cloudflare HTTPS → cloudflared → Uvicorn/FastAPI at 127.0.0.1:8014
```

`check` exercises the local app, catalog and a synthetic YYZ → AMS → CDG query.
`check --public` probes the configured HTTPS hostname instead. `status` reports the
configured local port and URL, managed processes and private log location. The app
serves the packaged frontend and read-only API; its database, logs and credentials are not web assets.

macOS launchd supervises the two processes after an explicit `start`. It restarts
an exited process; closing the terminal leaves them running. There is no login
item or boot service. The Mac must stay awake, online and logged in. After logout
or reboot, start Flyji manually again. Do not run `cloudflared service install`
or `brew services start cloudflared` for this setup.

To stop both processes and their automatic restart:

```sh
./deployment/flyji stop
```

This retains the runtime, token and hosted database. It does not remove the
Cloudflare tunnel or DNS route. To replace the token, stop first, run `tunnel`,
then start again. Update the app using the [installation instructions](../installation/README.md).

The scripts do not configure Cloudflare resources, change DNS or install paid
add-ons. Use the standard named HTTP tunnel and review the account's plan in the
dashboard. If port 8014 is occupied, the controller refuses to start; it does not
kill another process. Change a port only while stopped, and update both the
private settings and the dashboard's service URL.

This phase starts with twelve synthetic demo schedules in
`REPO/var/hosted/flights.db`. It runs no provider fetcher and creates no real flight
database. Fares and booking are outside the explorer. Anyone with access to the
published hostname can query these demo records; customer validation and real-data
deployment are later work.

## Real itinerary serving

Set `"data_mode": "real"` in the installed private `settings.json` and select an
explicit absolute `database_path` under the project's ignored `var/hosted/`.
Initialize that file with admin before installation/startup. Real mode validates
the `flight-legs` schema, accepts an empty initialized dataset and rejects demo
flight records; the seed step cannot insert demo data in this mode. Existing
synthetic installations keep their default mode and original smoke checks.

Install backend/frontend/admin together while the hosted jobs are stopped. Resume
a compatible producer against its separate ingestion file; publish explicitly to
the serving path. Never use an ingestion database as the serving file. `manage.py check` and `check --public`
validate real readiness and a route from the actual dataset, rather than hardcoded
demo airports/dates. `/health/ready` checks readable storage and exposes schema identity;
admin `overview --json` reports the file path and same schema. Historical ADSB records use
observed time bounds and partial coverage; they cannot supply future schedules.

Read concurrency, SQL deadlines, index upgrades and repeatable load probes are
described in [backend operations](../docs/backend-operations.md). Readiness no
longer counts flights or selects a time basis; use `/v1/catalog` for those facts.

Application overlays recorded in `installation.json` are verified before serving.
Replacing the installed wheel while retaining an overlay receipt fails startup
if it discards those fixes. Build subsequent deployments from a revision that
includes both the ingestion changes and the application fixes, then use the
installer to produce a fresh receipt. A successful readiness probe alone does
not establish that the intended routing version is running.

Browser asset URLs include a content hash for the complete frontend release.
They update automatically when files change; HTML is served with `no-store`.
The current and previous release are held in memory so a page loading during a
static-file update can finish using consistent files. A server restart clears
that history; an older page that has not finished loading may need a refresh.
No manual version bump is required.

The startup installation check has a three-minute deadline because it validates
real catalog aggregates and an end-to-end search before loading either job.
A failed or timed-out check leaves both jobs unloaded. This deadline applies to
the deployment preflight, not to user route searches or their coverage. Startup
readiness and ordinary HTTP probes retain a 20-second timeout. The catalog probe
allows three minutes for a cold statistics refresh, including when ingestion
publishes between checks and invalidates the cache. A catalog timeout still
fails the deployment check; it does not shorten or truncate user searches.

## Single-command deployment and optional monitoring

`./deployment/flyji deploy` combines the existing stop/install/start/local-check
sequence using the pinned submodule. The optional `--bobowatch-runtime-dir PATH`
provisions monitoring credentials during installation, and subsequent deployments
reuse the saved registration. It never starts BoboWatch. Hosted monitoring remains
opt-in and gated on BoboWatch's RAM-disk quota/lifecycle controls; see
[the integration notes](../docs/monitoring.md) before activation.
