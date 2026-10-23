# BoboWatch integration and rollout

Flightrepo now has an opt-in HTTP integration with the public
[BoboWatch repository](https://github.com/lichen-liu/bobowatch), pinned as the
`vendor/bobowatch` Git submodule. The pin includes its macOS installer, verified
five-GiB RAM-only storage, circular retention, live quota admin and on-demand
supervision. Enabling it remains an explicit operator deployment; this branch
starts no installed apps or user jobs.

## What is included

- One adapter per serving process, started during ASGI lifespan after fork.
- Request counts, status classes, latency histograms, active work, registered
  endpoint templates and observed HTTP body bytes, including streaming responses.
- Instrumentation outside the installed app's host/body middleware, so its 400/413
  rejections are counted. Uvicorn/Cloudflare rejections before ASGI are outside it.
- Background authenticated delivery, bounded retry and no request-path telemetry IO.
- Idempotent local registration/token provisioning during installation and updates.
- Live BoboWatch inventory reload and client endpoint/token/interval reload.
- No adapter package download or new application runtime dependency. The wheel
  contains eight pinned standard-library BoboWatch source modules. It excludes
  Waitress, monitoring SQL storage and the dashboard.

## Source, install and deployment

Both Python and browser CI initialize the pinned public submodule without a token.
For development, clone recursively or run:

```sh
git submodule update --init vendor/bobowatch
python -m pip install -e '.[dev]'
```

The installer also initializes the recorded submodule before building its wheel.
A normal install without monitoring configuration keeps monitoring disabled:

```sh
python3 installation/install.py
```

Install the private non-demo BoboWatch runtime first (normal user, macOS,
Python 3.11+). After merging the matching BoboWatch runtime PR:

```sh
cd ~/p/bobowatch
git switch main
git pull --ff-only
python3 installation/install.py
./deployment/watch start
./deployment/watch check
./deployment/watch token
```

Installation starts nothing. `start` explicitly loads a crash-restarted session;
`stop` unloads it; `serve` is a foreground alternative. No login/boot auto-start.
The admin token opens `http://127.0.0.1:8720`. From a phone on the same trusted LAN,
use the Mac's private IPv4 address and port 8720. The installed listener binds all
IPv4 interfaces; `127.0.0.1` on a phone is the phone itself.

Return to Flightrepo for opt-in installation:

```sh
cd ~/p/flightrepo
git switch main
git pull --ff-only
git submodule update --init vendor/bobowatch
```

To reinstall while stopped, the installation interface is:

```sh
python3 installation/install.py \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
```

This provisions credentials but starts neither Flightrepo nor BoboWatch. An
existing enabled registration is re-provisioned automatically on later installs,
reusing its identity/token. For the existing hosted installation, the single-command deployment interface is:

```sh
./deployment/flyji deploy \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
```

It initializes the pinned submodule, stops loaded Flightrepo jobs, installs and
provisions, starts the app/tunnel and checks local operation. It uses the existing
private tunnel token; first-time public tunnel setup is still required. A failed
update retains the installer's existing incomplete-marker behavior and leaves the
app stopped. It never starts/restarts BoboWatch. Existing `start`, `stop`, `status`
and `check` commands remain available. Installer preflight checks suppress
telemetry so test requests do not become hosted measurements.

## Configuration discovery

| Consumer | Configuration selection |
| --- | --- |
| Flightrepo installer/controller | `--runtime-dir`, then `FLYJI_RUNTIME_DIR`, then `~/Library/Application Support/fyi.boboji.fly` |
| Installed Flightrepo server | Its controller passes the resolved installed runtime; `settings.json` lives there |
| BoboWatch provisioning | Explicit `--bobowatch-runtime-dir`, then `BOBOWATCH_RUNTIME_DIR`, then the saved registration runtime, then `~/Library/Application Support/fyi.boboji.watch` on macOS |
| App adapter | The explicit installed Flightrepo `settings.json`; relative token paths are relative to that file |

Provisioning stores the selected BoboWatch directory and stable IDs under
`monitoring_registration` in Flightrepo's existing private settings. It writes
`tokens/bobowatch.token` with owner-only permissions and adds a `monitoring`
connection section. The running app only needs that connection section; it never
opens BoboWatch's configuration or monitoring database.

Existing hostname, port, flight database path and data mode are preserved.
Credentials never appear in the settings JSON, stdout, Git or the endpoint URL.
Provisioning is a same-user local operation and needs only BoboWatch's private
configuration/registration files; it does not require a running API or a mounted
monitoring database. The non-demo registry must already exist. It is not an
unauthenticated network registration endpoint.

The first installation chooses a stable host-derived replica identity, then saves
it for reuse even if the host name later changes. Set explicit IDs in
`monitoring_registration` before provisioning if needed. Concurrent workers or
independent installations need distinct replica IDs.

## Failure and update behavior

When BoboWatch is unavailable, Flightrepo continues serving. The sender retries in
the background; surviving cumulative counters can recover totals after a gap,
but not exact timing. An app restart can lose unsent counters/events. There is no
local telemetry database or durable queue in Flightrepo.

Edit the app's private `monitoring.endpoint` or `token_file` to change the delivery
target. The sender validates/reloads settings before its next delivery and keeps
the same counters/run UUID; invalid changes retain the prior target. A replica-ID
change requires an app restart. If monitoring was disabled at startup, enabling it
requires a restart. A running sender can suspend delivery with `enabled=false`.

BoboWatch reloads its inventory and credential files every two seconds. Invalid
changes retain the last valid configuration; listening address/database changes
require its restart. Tokens are scoped to one producer and cannot read admin
metrics. Same-machine delivery uses loopback; other hosts require HTTPS and a
separate provisioning/transport design.

## Review and update sequence

1. Merge the BoboWatch runtime PR, then the Flightrepo pin/rollout PR.
2. Install/start/check BoboWatch with the commands above.
3. Deploy Flightrepo with `--bobowatch-runtime-dir` to register and enable it.
4. Exercise Flightrepo, wait a delivery interval (about 15 seconds), exercise it
   again and refresh the dashboard. The first snapshot in fresh RAM history is
   a zero-delta baseline; later snapshots show new HTTP traffic.

Later Flightrepo reinstalls/deployments reuse the saved registration and token
without needing the flag again. Configuration, selected database, data mode and
existing tunnel credentials are preserved. Deployment stops the serving app/tunnel
but does not restart the independent ingestion worker or resize its RAM disk.

For a BoboWatch update, stop it, update its checkout, rerun its installer, then
start and check it. Surviving RAM history, credentials and quota are preserved.
Use `./deployment/watch admin storage quota 5GiB` for a live policy change.
The physical volume size is separate; increasing beyond it requires a stopped,
explicit release/reallocation, which loses history. Reboot or power loss may also
erase history; there are no automatic disk snapshots. See the complete
[BoboWatch operator guide](https://github.com/lichen-liu/bobowatch/blob/main/docs/runtime.md).

This integration does not sample CPU/memory, implement external uptime checks,
export database health or measure ingestion progress yet. It is a local
HTTP-monitoring pilot rather than a complete alerting system.
