# BoboWatch integration preparation

Flightrepo now has an opt-in HTTP integration with the public
[BoboWatch repository](https://github.com/lichen-liu/bobowatch), pinned as the
`vendor/bobowatch` Git submodule. This is preparation for a controlled pilot.
The hosted application and ingestion worker are not enabled or changed by this
branch. BoboWatch's RAM-disk lifecycle, five-GiB family quota, circular retention
and interactive admin controls remain the deployment gate before hosted activation.

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

Once the BoboWatch runtime/storage deployment gate is complete and its private
non-demo runtime is initialized, the opt-in installation interface is:

```sh
python3 installation/install.py \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
```

This provisions credentials but starts neither Flightrepo nor BoboWatch. An
existing enabled registration is re-provisioned automatically on later installs,
reusing its identity/token. The prepared single-command deployment interface is:

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

## Activation sequence

1. Review/merge BoboWatch's instrumentation/provisioning foundation.
2. Implement and validate its `fyi.boboji.watch` installer, RAM-only five-GiB
   circular storage, quota administration and explicitly started supervision.
3. Refresh this submodule pin to the reviewed compatible revision.
4. Use the existing deployment command with monitoring explicitly enabled, then
   verify real counters and failure recovery before treating it as monitoring.

This integration does not sample CPU/memory, implement external uptime checks,
export database health or measure ingestion progress yet.
