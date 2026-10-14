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
