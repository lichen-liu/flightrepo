# Install Flyji on this Mac

From the repository root, using Python 3.11+ with pip as your normal macOS user:

```sh
python3 installation/install.py
```

The installer builds and installs a wheel into a dedicated virtual environment at
`~/Library/Application Support/fyi.boboji.fly`. It downloads build and application
dependencies, copies the serving/control scripts, and checks the installed app.
It does not start either process or configure the public hostname.

The initial database is `var/hosted/flights.db` under this checkout. It contains
the twelve synthetic flights from the connection demo. The database and SQLite
sidecars are ignored by Git. An existing hosted database is preserved; this
installation does not fetch provider data or build a real flight database.

| Location | Contents |
|---|---|
| `fyi.boboji.fly/venv/` | Installed application and dependencies |
| `fyi.boboji.fly/manage.py`, `serve.py` | Process control and loopback server |
| `fyi.boboji.fly/settings.json` | Hostname, port and explicit database path |
| `fyi.boboji.fly/installation.json` | Source/build and dependency receipt |
| `fyi.boboji.fly/jobs/`, `logs/` | Manual job definitions and operational logs |
| `fyi.boboji.fly/tunnel-token` | Added separately through the hidden terminal prompt |
| `REPO/var/hosted/flights.db` | Persistent synthetic schedule data |

The runtime is owner-only. Editing the checkout does not change its installed app
snapshot. Keep the checkout available because the database remains there.
Continue with the [deployment instructions](../deployment/README.md).

To update, stop Flyji, check out the intended code, then reinstall and explicitly
start it:

```sh
./deployment/flyji stop
python3 installation/install.py
./deployment/flyji start
./deployment/flyji check
```

Updates preserve the token and existing hosted data. Installation refuses to
update a loaded job. A failed update leaves the runtime marked incomplete and
blocks startup until a successful installer rerun; it is not an atomic rollback.

Database verification prints phase progress. The seed/preservation check and
installed application check each have a three-minute subprocess deadline. The
preservation phase reports an exact record count once. Application startup checks
the serving schema and source identities without recomputing archive statistics.
In real mode, preflight selects admitted time evidence through bootstrap, then
exercises the real search and rebuild endpoints. Empty or unsearchable real
datasets are reported explicitly. A timeout leaves the incomplete marker and
previous receipt in place; review the named phase before retrying.

See [archive query measurements](../docs/archive-query-performance.md) for the
query plans, fixture timings and bounded measurements on the large archive.

For a custom runtime outside the checkout, set the same absolute directory for
installation and later commands:

```sh
export FLYJI_RUNTIME_DIR="/absolute/private/path/fyi.boboji.fly"
python3 installation/install.py
```

The installer and launcher also accept `--runtime-dir PATH` instead of the
environment variable; use the same path for each command. Only one Flyji instance
can be managed per login session. The hosted database still lives under the project.

Existing installations under `~/Library/Application Support/Flyji` can continue
using that directory by setting `FLYJI_RUNTIME_DIR` to its absolute path for both
installation and deployment commands. The new default does not move existing
settings or tunnel tokens automatically. Stop the existing instance before
installing in a different directory.

## Optional BoboWatch integration

The installer initializes the pinned `vendor/bobowatch` submodule and includes
only its standard-library adapter/provisioning modules in the wheel. Monitoring
remains disabled unless explicitly configured. See [monitoring integration](../docs/monitoring.md)
for the optional startup `--bobowatch-runtime-dir` interface and private
configuration paths. Installation does not read BoboWatch's runtime, provision
credentials or contact its API. It preserves existing monitoring settings.

After installation, opt in at startup:

```sh
./deployment/flyji start \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
```

`./deployment/flyji deploy` combines stop/install/start/check and accepts the same
startup option. An enabled sender can switch targets live with
`./deployment/flyji monitoring --bobowatch-runtime-dir PATH`.
