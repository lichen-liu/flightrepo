# Install Flyji on this Mac

From the repository root, using Python 3.11+ with pip as your normal macOS user:

```sh
python3 installation/install.py
```

The installer builds and installs a wheel into a dedicated virtual environment at
`~/Library/Application Support/Flyji`. It downloads build and application
dependencies, copies the serving/control scripts, and checks the installed app.
It does not start either process or configure the public hostname.

The initial database is `var/hosted/flights.db` under this checkout. It contains
the twelve synthetic flights from the connection demo. The database and SQLite
sidecars are ignored by Git. An existing hosted database is preserved; this
installation does not fetch provider data or build a real flight database.

| Location | Contents |
|---|---|
| `Flyji/venv/` | Installed application and dependencies |
| `Flyji/manage.py`, `serve.py` | Process control and loopback server |
| `Flyji/settings.json` | Hostname, port and explicit database path |
| `Flyji/installation.json` | Source/build and dependency receipt |
| `Flyji/jobs/`, `logs/` | Manual job definitions and operational logs |
| `Flyji/tunnel-token` | Added separately through the hidden terminal prompt |
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

For a custom runtime outside the checkout, set the same absolute directory for
installation and later commands:

```sh
export FLYJI_RUNTIME_DIR="/absolute/private/path/Flyji"
python3 installation/install.py
```

The installer and launcher also accept `--runtime-dir PATH` instead of the
environment variable; use the same path for each command. Only one Flyji instance
can be managed per login session. The hosted database still lives under the project.
