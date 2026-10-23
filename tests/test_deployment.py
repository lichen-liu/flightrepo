"""Installation/lifecycle failure checks without user jobs, credentials or publication."""

import asyncio
import importlib.util
import io
import json
import plistlib
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import OpenerDirector

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures/connections-demo.json"


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "deployment"))
    modules = {}
    for name, path in (
        ("serve", REPO / "deployment/serve.py"),
        ("manage", REPO / "deployment/manage.py"),
        ("flyji_install", REPO / "installation/install.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[name] = module
    monkeypatch.setattr(
        OpenerDirector,
        "open",
        lambda *args, **kwargs: pytest.fail("Deployment tests must stub network access"),
    )
    return SimpleNamespace(
        serve=modules["serve"], manage=modules["manage"], install=modules["flyji_install"]
    )


def private_file(path, content):
    path.write_bytes(content)
    path.chmod(0o600)


def runtime(path, database, *, controls=False):
    path.mkdir(mode=0o700)
    private_file(
        path / "settings.json",
        json.dumps(
            {"hostname": "fly.boboji.fyi", "port": 8014, "database_path": str(database)}
        ).encode(),
    )
    private_file(path / "synthetic-data.json", FIXTURE.read_bytes())
    if controls:
        for name in ("jobs", "logs", "venv"):
            (path / name).mkdir(mode=0o700)
        for name in ("installation.json", "manage.py", "serve.py"):
            private_file(
                path / name, b"{}\n" if name == "installation.json" else b"prior installed state\n"
            )
        private_file(path / "tunnel-token", b"unit-test-placeholder-token\n")
    return path


@pytest.fixture
def installed(tmp_path, scripts):
    root = runtime(tmp_path / "runtime", tmp_path / "hosted/flights.db")
    scripts.serve.seed_database(root)
    return root


@pytest.mark.parametrize("running", [False, True])
def test_status_reports_configured_port(tmp_path, scripts, monkeypatch, capsys, running):
    root = runtime(tmp_path / "runtime", tmp_path / "flights.db")
    settings = json.loads((root / "settings.json").read_text())
    settings["port"] = 9123
    private_file(root / "settings.json", json.dumps(settings).encode())
    monkeypatch.setattr(
        scripts.manage,
        "launchctl",
        lambda *args: SimpleNamespace(
            returncode=0 if running else 1, stdout="\tstate = running\n\tpid = 123\n"
        ),
    )
    monkeypatch.setattr(scripts.manage, "process_name", lambda pid: "python")

    scripts.manage.status(root)

    output = capsys.readouterr().out
    assert "http://127.0.0.1:9123/ (configured port: 9123)" in output
    assert "PID: 123; process: python" in output if running else "PID: none" in output


def test_default_runtime_uses_reverse_domain_directory(tmp_path, scripts, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FLYJI_RUNTIME_DIR", raising=False)
    root = tmp_path / "Library/Application Support/fyi.boboji.fly"
    assert scripts.manage.runtime_root() == root
    assert scripts.install.runtime_root() == root
    assert scripts.serve.RUNTIME_ROOT.name == "fyi.boboji.fly"

    (root / "venv/bin").mkdir(parents=True)
    python = root / "venv/bin/python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$0" "$@"\n')
    python.chmod(0o700)
    (root / "manage.py").touch()
    result = subprocess.run(
        [str(REPO / "deployment/flyji"), "status"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [str(python), str(root / "manage.py"), "status"]


def test_runtime_environment_override_is_preserved(tmp_path, scripts, monkeypatch):
    root = tmp_path / "custom-runtime"
    monkeypatch.setenv("FLYJI_RUNTIME_DIR", str(root))
    assert scripts.manage.runtime_root() == root
    assert scripts.install.runtime_root() == root


def test_synthetic_seed_is_absent_only_and_preserves_modified_facts(installed, scripts):
    database = Path(scripts.serve.read_settings(installed)["database_path"])
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE flights SET flight_number='MANUALLY-ADJUSTED' WHERE provider_id=(SELECT provider_id FROM flights LIMIT 1)"
        )
    before = database.read_bytes()
    result = scripts.serve.seed_database(installed)
    assert not result["seeded"] and result["flight_count"] == 12
    assert database.read_bytes() == before
    with closing(sqlite3.connect(database)) as connection, connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM flights WHERE flight_number='MANUALLY-ADJUSTED'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("mixed", [False, True])
def test_existing_real_or_mixed_database_is_rejected_unchanged(installed, scripts, mixed):
    database = Path(scripts.serve.read_settings(installed)["database_path"])
    with closing(sqlite3.connect(database)) as connection, connection:
        suffix = " WHERE provider_id=(SELECT provider_id FROM flights LIMIT 1)" if mixed else ""
        connection.execute("UPDATE flights SET provider='real-provider'" + suffix)
    before = database.read_bytes()
    with pytest.raises(ValueError, match="only demo"):
        scripts.serve.seed_database(installed)
    with pytest.raises(ValueError, match="only demo"):
        scripts.serve.create_server(installed)
    assert database.read_bytes() == before


def test_invalid_seed_never_publishes_database(tmp_path, scripts):
    database = tmp_path / "hosted/flights.db"
    root = runtime(tmp_path / "runtime", database)
    data = json.loads(FIXTURE.read_text())
    data["flights"][0]["provider"] = "real-provider"
    private_file(root / "synthetic-data.json", json.dumps(data).encode())
    with pytest.raises(ValueError, match="only nonempty demo"):
        scripts.serve.seed_database(root)
    assert not database.exists()


def test_missing_database_check_never_creates_it(tmp_path, scripts):
    database = tmp_path / "missing.db"
    root = runtime(tmp_path / "runtime", database)
    with pytest.raises(sqlite3.Error):
        asyncio.run(scripts.serve.check_installation(root))
    assert not database.exists()


def test_installed_check_and_loopback_configuration(installed, scripts):
    asyncio.run(scripts.serve.check_installation(installed))
    app, options = scripts.serve.create_server(installed)
    assert options["host"] == "127.0.0.1" and options["port"] == 8014
    assert options["workers"] == 1 and not options["reload"]
    assert 1 <= options["limit_concurrency"] <= 64 and options["forwarded_allow_ips"] == "127.0.0.1"
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/vendor/world-110m.geojson").status_code == 200
        assert client.post("/v1/admin/ingestions").status_code == 404
        assert client.get("/", headers={"Host": "unrelated.invalid"}).status_code == 400
        assert client.get("/", headers={"Host": "fly.boboji.fyi"}).status_code == 200


def test_multiple_app_instances_keep_database_bindings_and_original_app(
    installed, tmp_path, scripts
):
    from core.config import get_settings
    from server.api import app as original
    from server.api import get_store

    overrides = dict(original.dependency_overrides)
    settings = get_settings()
    caches = get_store.cache_info(), get_settings.cache_info()
    first, _ = scripts.serve.create_server(installed)
    second_root = runtime(tmp_path / "second", tmp_path / "second-data/flights.db")
    scripts.serve.seed_database(second_root)
    database = scripts.serve.read_settings(second_root)["database_path"]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "DELETE FROM flights WHERE provider_id=(SELECT provider_id FROM flights LIMIT 1)"
        )
    second, _ = scripts.serve.create_server(second_root)
    with (
        TestClient(first, base_url="http://127.0.0.1") as a,
        TestClient(second, base_url="http://127.0.0.1") as b,
    ):
        assert a.get("/v1/catalog").json()["dataset"]["flight_count"] == 12
        assert b.get("/v1/catalog").json()["dataset"]["flight_count"] == 11
        assert a.get("/v1/catalog").json()["dataset"]["flight_count"] == 12
    assert original.dependency_overrides == overrides
    assert get_store.cache_info() == caches[0]
    assert get_settings() is settings
    assert get_settings.cache_info().misses == caches[1].misses


@pytest.mark.parametrize(
    "updates",
    [
        {"port": True},
        {"port": 1023},
        {"port": 65536},
        {"database_path": "relative.db"},
        {"hostname": "fly.boboji.fyi/path"},
        {"hostname": "*.boboji.fyi"},
    ],
)
def test_invalid_settings_fail_before_listener(installed, scripts, updates):
    values = json.loads((installed / "settings.json").read_text()) | updates
    private_file(installed / "settings.json", json.dumps(values).encode())
    with pytest.raises(ValueError):
        scripts.serve.create_server(installed)


def test_missing_database_path_rejected_before_seed(tmp_path, scripts, monkeypatch):
    root = runtime(tmp_path / "runtime", tmp_path / "explicit.db")
    private_file(root / "settings.json", b'{"hostname":"fly.boboji.fyi","port":8014}')
    source_root = tmp_path / "source-root"
    monkeypatch.setattr(scripts.serve, "REPO", source_root)
    with pytest.raises(ValueError, match="database_path"):
        scripts.serve.read_settings(root)
    with pytest.raises(ValueError, match="database_path"):
        scripts.serve.seed_database(root)
    assert not (source_root / "var").exists() and not (tmp_path / "explicit.db").exists()


def test_missing_database_path_refuses_installer_before_marker(
    tmp_path, checkout, scripts, monkeypatch
):
    root = runtime(tmp_path / "runtime", checkout / "var/hosted/flights.db")
    settings = b'{"hostname":"fly.boboji.fyi","port":8014}'
    private_file(root / "settings.json", settings)
    monkeypatch.setattr(scripts.install, "loaded", lambda job: False)
    monkeypatch.setattr(
        scripts.install.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Invalid settings must precede build/install"),
    )
    with pytest.raises(ValueError, match="database_path"):
        scripts.install.install(root, checkout)
    assert not (root / "installing").exists()
    assert not (checkout / "var/hosted").exists()
    assert (root / "settings.json").read_bytes() == settings


def test_copied_helper_missing_database_path_fails_without_seeding(tmp_path, scripts, monkeypatch):
    root = runtime(tmp_path / "copied-runtime", tmp_path / "explicit.db")
    private_file(root / "settings.json", b'{"hostname":"fly.boboji.fyi","port":8014}')
    copied = root / "serve.py"
    private_file(copied, (REPO / "deployment/serve.py").read_bytes())
    spec = importlib.util.spec_from_file_location("flyji_copied_serve", copied)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "flyji_copied_serve", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "umask", lambda mask: 0o077)
    with pytest.raises(SystemExit, match="database_path"):
        module.main(["--root", str(root), "--seed"])
    assert not (tmp_path / "var").exists() and not (tmp_path / "explicit.db").exists()


def body_probe(scripts, chunks, headers=(), *, slow=False):
    calls, output = [], []

    async def downstream(scope, receive, send):
        calls.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages = iter(
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    )

    async def receive():
        if slow:
            await asyncio.sleep(0.02)
        return next(messages, {"type": "http.disconnect"})

    async def send(message):
        output.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": list(headers),
        "http_version": "1.1",
        "scheme": "http",
    }
    asyncio.run(scripts.serve.BoundedBody(downstream)(scope, receive, send))
    return next(x["status"] for x in output if x["type"] == "http.response.start"), calls


@pytest.mark.parametrize("declared", [None, b"1"])
def test_actual_chunked_size_bound_does_not_trust_content_length(scripts, declared):
    limit = scripts.serve.MAX_REQUEST_BODY
    headers = [] if declared is None else [(b"content-length", declared)]
    status, calls = body_probe(scripts, [b"x" * (limit // 2), b"x" * (limit // 2 + 1)], headers)
    assert status == 413 and calls == []


@pytest.mark.parametrize(
    "declared,status", [(b"-1", 400), (b"not-a-number", 400), (b"999999999", 413)]
)
def test_declared_body_errors_are_rejected_before_endpoint(scripts, declared, status):
    actual, calls = body_probe(scripts, [b""], [(b"content-length", declared)])
    assert actual == status and calls == []


def test_body_at_bound_is_delivered_once(scripts):
    limit = scripts.serve.MAX_REQUEST_BODY
    status, calls = body_probe(scripts, [b"x" * (limit // 2), b"y" * (limit - limit // 2)])
    assert status == 204 and len(calls) == 1
    assert len(calls[0]["body"]) == limit and not calls[0]["more_body"]


def test_total_body_timeout_is_a_readable_failure(scripts, monkeypatch):
    timeout = asyncio.timeout
    monkeypatch.setattr(scripts.serve.asyncio, "timeout", lambda seconds: timeout(0.001))
    status, calls = body_probe(scripts, [b""], slow=True)
    assert status == 408 and calls == []


def test_slow_oversize_response_does_not_emit_second_timeout_response(scripts, monkeypatch):
    timeout = asyncio.timeout
    monkeypatch.setattr(scripts.serve.asyncio, "timeout", lambda seconds: timeout(0.001))
    starts, called, stalled = [], [], False

    async def downstream(scope, receive, send):
        called.append(True)

    async def receive():
        return {
            "type": "http.request",
            "body": b"x" * (scripts.serve.MAX_REQUEST_BODY + 1),
            "more_body": False,
        }

    async def send(message):
        nonlocal stalled
        if message["type"] == "http.response.start":
            starts.append(message["status"])
        elif starts == [413] and not stalled:
            stalled = True
            await asyncio.sleep(0.02)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
    }
    asyncio.run(scripts.serve.BoundedBody(downstream)(scope, receive, send))
    assert starts == [413] and not called and stalled


def install_boundary(monkeypatch, scripts, *, fail=None):
    commands = []
    monkeypatch.setattr(scripts.install, "loaded", lambda job: False)

    def make_venv(path):
        (path / "bin").mkdir(exist_ok=True)
        private_file(path / "bin/python", b"mock Python\n")

    monkeypatch.setattr(
        scripts.install.venv, "EnvBuilder", lambda **kw: SimpleNamespace(create=make_venv)
    )

    def run(command, **kwargs):
        commands.append(command)
        phase = (
            "wheel"
            if "wheel" in command
            else "pip"
            if "install" in command
            else "seed"
            if "--seed" in command
            else "check"
            if "--check" in command
            else None
        )
        if fail is not None and phase == fail:
            raise subprocess.CalledProcessError(1, command)
        if phase == "wheel":
            directory = Path(command[command.index("--wheel-dir") + 1])
            (directory / "flight_routes_engine-0.1.0-py3-none-any.whl").write_bytes(
                b"mock app wheel"
            )
        elif phase == "seed":
            scripts.serve.seed_database(Path(command[1]).parent)
        elif phase == "check":
            asyncio.run(scripts.serve.check_installation(Path(command[1]).parent))
        stdout = (
            "a" * 40
            if command[:3] == ["git", "rev-parse", "HEAD"]
            else "fastapi==test\n"
            if command[-1] == "freeze"
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(scripts.install.subprocess, "run", run)
    return commands


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "deployment").mkdir(parents=True)
    (repo / "fixtures").mkdir()
    (repo / "var").mkdir(mode=0o700)
    for name in ("manage.py", "serve.py"):
        (repo / "deployment" / name).write_bytes((REPO / "deployment" / name).read_bytes())
    (repo / "fixtures/connections-demo.json").write_bytes(FIXTURE.read_bytes())
    return repo


@pytest.mark.parametrize("job", ["server", "tunnel"])
def test_loaded_job_refuses_install_before_state_changes(
    tmp_path, checkout, scripts, monkeypatch, job
):
    root = tmp_path / "not-created"
    monkeypatch.setattr(scripts.install, "loaded", lambda candidate: candidate == job)
    with pytest.raises(ValueError, match="Stop Flyji"):
        scripts.install.install(root, checkout)
    assert not root.exists() and not (checkout / "var/hosted").exists()


def test_successful_install_clears_marker_and_never_starts_jobs(
    tmp_path, checkout, scripts, monkeypatch
):
    root = tmp_path / "runtime"
    commands = install_boundary(monkeypatch, scripts)
    scripts.install.install(root, checkout)
    assert not (root / "installing").exists()
    assert (root / "synthetic-data.json").read_bytes() == FIXTURE.read_bytes()
    assert scripts.serve.validate_database(checkout / "var/hosted/flights.db") == 12
    receipt = json.loads((root / "installation.json").read_text())
    assert receipt["data_mode"] == "synthetic" and len(receipt["wheel_sha256"]) == 64
    assert root.stat().st_mode & 0o077 == 0
    assert not any(
        "launchctl" in str(x) or "cloudflared" in str(x) for command in commands for x in command
    )
    assert not (root / "tunnel-token").exists()


@pytest.mark.parametrize("phase", ["wheel", "pip", "seed", "check"])
def test_failed_install_keeps_marker_data_token_and_previous_receipt(
    tmp_path, checkout, scripts, monkeypatch, phase
):
    database = checkout / "var/hosted/flights.db"
    root = runtime(tmp_path / "runtime", database, controls=True)
    scripts.serve.seed_database(root)
    before = {
        path: path.read_bytes()
        for path in (database, root / "tunnel-token", root / "installation.json")
    }
    install_boundary(monkeypatch, scripts, fail=phase)
    with pytest.raises(subprocess.CalledProcessError):
        scripts.install.install(root, checkout)
    assert (root / "installing").exists()
    assert all(path.read_bytes() == content for path, content in before.items())
    monkeypatch.setattr(scripts.manage, "loaded", lambda job: False)
    with pytest.raises(ValueError, match="incomplete"):
        scripts.manage.start(root)


@pytest.mark.parametrize("name", ["flights.db", "connections-demo.db"])
def test_normal_and_demo_database_paths_refused_even_after_normalization(
    tmp_path, checkout, scripts, monkeypatch, name
):
    root = runtime(tmp_path / "runtime", checkout / "var/hosted/.." / name)
    monkeypatch.setattr(scripts.install, "loaded", lambda job: False)
    with pytest.raises(ValueError, match="separate hosted"):
        scripts.install.install(root, checkout)
    assert not (root / "installing").exists() and not (checkout / "var" / name).exists()


def test_private_files_reject_symlinks_and_public_modes(tmp_path, scripts):
    target = tmp_path / "target"
    private_file(target, b"keep")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="only by you"):
        scripts.manage.private_write(link, b"replace")
    assert target.read_bytes() == b"keep"
    target.chmod(0o644)
    with pytest.raises(ValueError, match="only by you"):
        scripts.manage.require_private(target)


def test_token_hidden_private_and_absent_from_job_definitions(
    tmp_path, scripts, monkeypatch, capsys
):
    root = runtime(tmp_path / "runtime", tmp_path / "db", controls=True)
    token = "test-placeholder-only"
    monkeypatch.setattr(scripts.manage, "loaded", lambda job: False)
    monkeypatch.setattr(scripts.manage.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    prompts = []
    monkeypatch.setattr(
        scripts.manage.getpass, "getpass", lambda prompt: prompts.append(prompt) or token
    )
    scripts.manage.save_token(root)
    assert prompts and "hidden" in prompts[0]
    assert token not in capsys.readouterr().out
    assert (root / "tunnel-token").read_text().strip() == token
    assert (root / "tunnel-token").stat().st_mode & 0o077 == 0
    jobs = scripts.manage.job_definitions(root, "/placeholder/cloudflared")
    assert token.encode() not in plistlib.dumps(jobs)
    for job in jobs.values():
        assert job["KeepAlive"] and job["Umask"] == 0o077
        assert job["WorkingDirectory"] == str(root)
        assert "LaunchAgents" not in json.dumps(job) and "LaunchDaemons" not in json.dumps(job)
    assert "--token-file" in jobs["tunnel"]["ProgramArguments"]


def start_boundaries(scripts, monkeypatch):
    monkeypatch.setattr(scripts.manage, "connector_binary", lambda: "/placeholder/cloudflared")
    monkeypatch.setattr(
        scripts.manage.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(scripts.manage, "wait_for_server", lambda port: None)

    class Available:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def bind(self, address):
            pass

    monkeypatch.setattr(scripts.manage.socket, "socket", Available)


@pytest.mark.parametrize("validation_seconds", [45, 90])
def test_large_archive_preflight_finishes_before_jobs_load(
    tmp_path, scripts, monkeypatch, validation_seconds
):
    root = runtime(tmp_path / "runtime", tmp_path / "db", controls=True)
    start_boundaries(scripts, monkeypatch)
    monkeypatch.setattr(scripts.manage, "loaded", lambda job: False)
    validated, launches = [], []

    def check(command, **kwargs):
        if kwargs["timeout"] < validation_seconds:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        validated.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def launch(*args):
        assert validated, "Validate the installation before starting either job"
        launches.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(scripts.manage.subprocess, "run", check)
    monkeypatch.setattr(scripts.manage, "launchctl", launch)
    scripts.manage.start(root)
    assert validated[0][-1] == "--check"
    assert len(launches) == 2 and all(args[0] == "bootstrap" for args in launches)


def test_preflight_timeout_keeps_both_jobs_unloaded(tmp_path, scripts, monkeypatch):
    root = runtime(tmp_path / "runtime", tmp_path / "db", controls=True)
    start_boundaries(scripts, monkeypatch)
    monkeypatch.setattr(scripts.manage, "loaded", lambda job: False)
    launches = []
    monkeypatch.setattr(scripts.manage, "launchctl", lambda *args: launches.append(args))

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(scripts.manage.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        scripts.manage.start(root)
    assert launches == [] and not list((root / "jobs").iterdir())


def test_port_occupied_never_bootstraps_or_kills_other_process(tmp_path, scripts, monkeypatch):
    root = runtime(tmp_path / "runtime", tmp_path / "db", controls=True)
    start_boundaries(scripts, monkeypatch)
    monkeypatch.setattr(scripts.manage, "loaded", lambda job: False)
    calls = []
    monkeypatch.setattr(scripts.manage, "launchctl", lambda *args: calls.append(args))

    class Occupied:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def bind(self, address):
            raise OSError("occupied")

    monkeypatch.setattr(scripts.manage.socket, "socket", Occupied)
    with pytest.raises(ValueError, match="occupied"):
        scripts.manage.start(root)
    assert calls == [] and not list((root / "jobs").iterdir())


def test_partial_start_unloads_new_server_job(tmp_path, scripts, monkeypatch):
    root = runtime(tmp_path / "runtime", tmp_path / "db", controls=True)
    start_boundaries(scripts, monkeypatch)
    state, calls = set(), []
    monkeypatch.setattr(scripts.manage, "loaded", lambda job: job in state)

    def launch(*args):
        calls.append(args)
        if args[0] == "bootstrap":
            job = "server" if scripts.manage.LABELS["server"] in args[2] else "tunnel"
            if job == "tunnel":
                return subprocess.CompletedProcess(args, 1, "", "connector failed")
            state.add(job)
        elif args[0] == "bootout":
            state.discard("server")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(scripts.manage, "launchctl", launch)
    with pytest.raises(RuntimeError, match="tunnel"):
        scripts.manage.start(root)
    assert not state and any(args[0] == "bootout" and args[1].endswith(".server") for args in calls)


@pytest.mark.parametrize("raised", [False, True])
def test_stop_attempts_both_jobs_even_if_tunnel_cannot_unload(scripts, monkeypatch, raised):
    state, calls = {"server", "tunnel"}, []
    monkeypatch.setattr(scripts.manage, "loaded", lambda job: job in state)

    def launch(*args):
        calls.append(args)
        if args[1].endswith(".tunnel"):
            if raised:
                raise subprocess.TimeoutExpired("launchctl", 20)
            return subprocess.CompletedProcess(args, 1, "", "failure")
        state.discard("server")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(scripts.manage, "launchctl", launch)
    with pytest.raises(RuntimeError, match="tunnel"):
        scripts.manage.stop()
    assert len(calls) == 2 and state == {"tunnel"}


class Response(io.BytesIO):
    status = 200

    def __init__(self, content, url):
        super().__init__(content)
        self.url = url

    def geturl(self):
        return self.url


def stub_requests(scripts, monkeypatch, open_url):
    monkeypatch.setattr(
        scripts.manage, "build_opener", lambda *handlers: SimpleNamespace(open=open_url)
    )


def public_responses():
    return {
        "/": b"<title>Flyji</title>",
        "/assets/app.mjs": b"via_airport",
        "/health/ready": {"status": "ready"},
        "/v1/catalog": {
            "dataset": {
                "data_mode": "synthetic",
                "flight_count": 12,
                "first_departure": "2026-10-17T18:00:00Z",
            }
        },
        "/v1/itineraries/search": {
            "items": [
                {
                    "legs": [
                        {"origin_iata": "YYZ", "destination_iata": "AMS"},
                        {"origin_iata": "AMS", "destination_iata": "CDG"},
                    ]
                }
            ]
        },
    }


@pytest.mark.parametrize(
    "path,value",
    [
        ("/", b"another site"),
        ("/assets/app.mjs", b"outdated"),
        ("/health/ready", {"status": "unavailable"}),
        ("/v1/catalog", {"dataset": {"data_mode": "real", "flight_count": 12}}),
        ("/v1/itineraries/search", {"items": []}),
    ],
)
def test_public_probe_requires_product_responses_not_job_state(scripts, monkeypatch, path, value):
    bodies = public_responses() | {path: value}

    def open_url(request, **kwargs):
        value = bodies[urlsplit(request.full_url).path]
        return Response(
            value if isinstance(value, bytes) else json.dumps(value).encode(), request.full_url
        )

    stub_requests(scripts, monkeypatch, open_url)
    with pytest.raises(ValueError):
        scripts.manage.probe("https://fly.boboji.fyi")


def test_public_check_uses_https_and_actual_sample_search(installed, scripts, monkeypatch, capsys):
    requests = []
    bodies = public_responses()

    def open_url(request, **kwargs):
        requests.append(request)
        value = bodies[urlsplit(request.full_url).path]
        return Response(
            value if isinstance(value, bytes) else json.dumps(value).encode(), request.full_url
        )

    stub_requests(scripts, monkeypatch, open_url)
    monkeypatch.setattr(
        scripts.manage, "loaded", lambda job: pytest.fail("Process status cannot prove a live URL")
    )
    scripts.manage.check(installed, public=True)
    assert all(request.full_url.startswith("https://fly.boboji.fyi/") for request in requests)
    sample = json.loads(requests[-1].data)
    assert (
        sample["origin"] == "YYZ"
        and sample["destination"] == "CDG"
        and sample["via_airport"] == "AMS"
    )
    assert "Verified live" in capsys.readouterr().out


def test_public_unreachable_is_not_reported_success(installed, scripts, monkeypatch, capsys):
    stub_requests(
        scripts, monkeypatch, lambda *args, **kwargs: (_ for _ in ()).throw(URLError("unreachable"))
    )
    with pytest.raises(URLError):
        scripts.manage.check(installed, public=True)
    assert "Verified" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "destination,allowed",
    [
        ("https://fly.boboji.fyi/workspace", True),
        ("https://fly.boboji.fyi:443/workspace", True),
        ("https://other.invalid/workspace", False),
        ("http://fly.boboji.fyi/workspace", False),
        ("https://fly.boboji.fyi:444/workspace", False),
    ],
)
def test_public_redirect_requires_same_https_origin(scripts, monkeypatch, destination, allowed):
    bodies = public_responses()
    followed = []

    def opener(*handlers):
        def open_url(request, **kwargs):
            path = urlsplit(request.full_url).path
            if path == "/":
                redirected = handlers[0].redirect_request(
                    request, None, 302, "redirect", {}, destination
                )
                followed.append(redirected.full_url)
                return Response(bodies["/"], redirected.full_url)
            value = bodies[path]
            return Response(
                value if isinstance(value, bytes) else json.dumps(value).encode(), request.full_url
            )

        return SimpleNamespace(open=open_url)

    monkeypatch.setattr(scripts.manage, "build_opener", opener)
    if allowed:
        assert scripts.manage.probe("https://fly.boboji.fyi")["route_search"] == "passed"
        assert followed == [destination]
    else:
        with pytest.raises(ValueError, match="redirected away"):
            scripts.manage.probe("https://fly.boboji.fyi")
        assert followed == []


def test_public_final_response_origin_is_verified(scripts, monkeypatch):
    stub_requests(
        scripts,
        monkeypatch,
        lambda request, **kwargs: Response(b"<title>Flyji</title>", "https://other.invalid/"),
    )
    with pytest.raises(ValueError, match="configured Flyji origin"):
        scripts.manage.probe("https://fly.boboji.fyi")


@pytest.mark.parametrize(
    "path,value",
    [
        ("/health/ready", []),
        ("/v1/catalog", []),
        ("/v1/catalog", {"dataset": []}),
        ("/v1/catalog", {"dataset": {"data_mode": "synthetic", "flight_count": True}}),
        ("/v1/catalog", {"dataset": {"data_mode": "synthetic", "flight_count": "12"}}),
        ("/v1/catalog", {"dataset": {"data_mode": "synthetic", "flight_count": 0}}),
        (
            "/v1/catalog",
            {"dataset": {"data_mode": "synthetic", "flight_count": 12, "first_departure": []}},
        ),
        ("/v1/itineraries/search", []),
        ("/v1/itineraries/search", {"items": {}}),
        ("/v1/itineraries/search", {"items": [0]}),
        ("/v1/itineraries/search", {"items": [{"legs": {}}]}),
        ("/v1/itineraries/search", {"items": [{"legs": [None]}]}),
        (
            "/v1/itineraries/search",
            {"items": [{"legs": [{"origin_iata": "YYZ", "destination_iata": "CDG"}]}]},
        ),
    ],
)
def test_wrong_shaped_public_json_is_a_readable_failure(scripts, monkeypatch, path, value):
    bodies = public_responses() | {path: value}

    def open_url(request, **kwargs):
        value = bodies[urlsplit(request.full_url).path]
        return Response(
            value if isinstance(value, bytes) else json.dumps(value).encode(), request.full_url
        )

    stub_requests(scripts, monkeypatch, open_url)
    with pytest.raises(ValueError):
        scripts.manage.probe("https://fly.boboji.fyi")


def test_failed_install_can_recover_without_resetting_data(
    tmp_path, checkout, scripts, monkeypatch
):
    database = checkout / "var/hosted/flights.db"
    root = runtime(tmp_path / "runtime", database, controls=True)
    scripts.serve.seed_database(root)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE flights SET flight_number='RETAINED-EDIT'")
    before = database.read_bytes()
    token = (root / "tunnel-token").read_bytes()
    install_boundary(monkeypatch, scripts, fail="pip")
    with pytest.raises(subprocess.CalledProcessError):
        scripts.install.install(root, checkout)
    install_boundary(monkeypatch, scripts)
    scripts.install.install(root, checkout)
    assert not (root / "installing").exists()
    assert database.read_bytes() == before and (root / "tunnel-token").read_bytes() == token


def test_seed_race_retains_winning_existing_synthetic_facts(tmp_path, scripts, monkeypatch):
    winner = runtime(tmp_path / "winner-runtime", tmp_path / "winner-data/flights.db")
    scripts.serve.seed_database(winner)
    winner_database = Path(scripts.serve.read_settings(winner)["database_path"])
    with closing(sqlite3.connect(winner_database)) as connection, connection:
        connection.execute("UPDATE flights SET flight_number='WINNING-WRITER'")
    winner_bytes = winner_database.read_bytes()
    destination = tmp_path / "contended/flights.db"
    root = runtime(tmp_path / "other-runtime", destination)

    def competing_publication(source, target):
        private_file(target, winner_bytes)
        raise FileExistsError("another installer published first")

    monkeypatch.setattr(scripts.serve.os, "link", competing_publication)
    result = scripts.serve.seed_database(root)
    assert not result["seeded"] and destination.read_bytes() == winner_bytes


def test_corrupt_existing_database_is_preserved(tmp_path, scripts):
    database = tmp_path / "corrupt.db"
    private_file(database, b"not a SQLite database")
    root = runtime(tmp_path / "runtime", database)
    with pytest.raises(sqlite3.Error):
        scripts.serve.seed_database(root)
    assert database.read_bytes() == b"not a SQLite database"


@pytest.mark.parametrize(
    "version,accepted",
    [("2025.3.1", False), ("2025.4.0", True), ("2026.10.1", True), ("unknown", False)],
)
def test_connector_minimum_token_file_version(scripts, monkeypatch, version, accepted):
    monkeypatch.setattr(scripts.manage.shutil, "which", lambda name: "/placeholder/cloudflared")
    monkeypatch.setattr(
        scripts.manage.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, 0, f"cloudflared version {version}", ""
        ),
    )
    if accepted:
        assert scripts.manage.connector_binary().endswith("cloudflared")
    else:
        with pytest.raises(ValueError, match="2025.4.0"):
            scripts.manage.connector_binary()


@pytest.mark.parametrize("status,size", [(503, 0), (200, 1_000_001)])
def test_public_response_failure_or_size_limit_not_accepted(scripts, monkeypatch, status, size):
    def open_url(request, **kwargs):
        response = Response(b"x" * size, request.full_url)
        response.status = status
        return response

    stub_requests(scripts, monkeypatch, open_url)
    with pytest.raises(ValueError, match="Unexpected or oversized"):
        scripts.manage.probe("https://fly.boboji.fyi")


def test_real_mode_serves_observed_legs_without_seeding_demo(tmp_path, scripts):
    from test_adsblol_extraction import run
    from test_adsblol_legs import leg_trace

    root = tmp_path / "runtime"
    root.mkdir()
    database = tmp_path / "real.db"
    from ingestion.publish import publish_day

    staging = tmp_path / "ingestion.db"
    run(tmp_path / "inputs", date(2025, 1, 1), [leg_trace()], output_db=staging)
    publish_day(staging, database, date(2025, 1, 1))
    (root / "settings.json").write_text(
        json.dumps(
            {
                "hostname": "fly.boboji.fyi",
                "port": 8014,
                "database_path": str(database),
                "data_mode": "real",
            }
        )
    )
    result = scripts.serve.seed_database(root)
    assert result["seeded"] is False and result["flight_count"] == 1
    asyncio.run(scripts.serve.check_installation(root))
    application, _ = scripts.serve.create_server(root)
    with TestClient(application, base_url="https://fly.boboji.fyi") as client:
        response = client.get("/v1/catalog")
        assert response.status_code == 200
        assert response.json()["dataset"]["data_mode"] == "real"
        assert "providers" not in response.json()["dataset"]
        assert client.get("/health/ready").json()["schema_contract"] == "flight-legs"


def test_real_mode_requires_an_initialized_database_and_rejects_demo(tmp_path, scripts):
    from core.models import FlightRecord
    from ingestion.updater import FlightUpdater

    root = tmp_path / "runtime"
    root.mkdir()
    database = tmp_path / "real.db"
    (root / "settings.json").write_text(
        json.dumps(
            {
                "hostname": "fly.boboji.fyi",
                "port": 8014,
                "database_path": str(database),
                "data_mode": "real",
            }
        )
    )
    with pytest.raises(ValueError, match="never seeds"):
        scripts.serve.seed_database(root)
    assert not database.exists()
    updater = FlightUpdater(database)
    updater.initialize()
    assert scripts.serve.seed_database(root)["flight_count"] == 0
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/connections-demo.json").read_text())
    updater.upsert([FlightRecord.model_validate(fixture["flights"][0])])
    with pytest.raises(ValueError, match="demo flight records"):
        scripts.serve.create_server(root)


def test_startup_readiness_allows_archive_aggregation_without_request_flood(scripts, monkeypatch):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(scripts.manage.time, "monotonic", lambda: clock[0])

    class Response(io.BytesIO):
        status = 200

    def ready(url, timeout):
        timeouts.append(timeout)
        # A normal archive query can take several seconds, not one second.
        assert timeout >= 4
        clock[0] += 4
        return Response(b'{"status":"ready"}')

    monkeypatch.setattr(scripts.manage, "urlopen", ready)
    scripts.manage.wait_for_server(8014)
    assert timeouts == [20]


def test_startup_readiness_deadline_is_bounded_after_timeouts(scripts, monkeypatch):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(scripts.manage.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        scripts.manage.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def unavailable(url, timeout):
        timeouts.append(timeout)
        clock[0] += timeout
        raise TimeoutError("slow readiness")

    monkeypatch.setattr(scripts.manage, "urlopen", unavailable)
    with pytest.raises(RuntimeError, match="did not become ready"):
        scripts.manage.wait_for_server(8014)
    assert len(timeouts) == 3 and max(timeouts) <= 20
    assert clock[0] <= 60.2


@pytest.mark.parametrize("catalog_seconds", [45, 181])
def test_public_probe_allows_cold_catalog_with_bounded_requests(
    scripts, monkeypatch, catalog_seconds
):
    bodies = public_responses()
    budgets = {}

    def open_url(request, timeout):
        path = urlsplit(request.full_url).path
        budgets[path] = timeout
        # Publication can invalidate the catalog cache; no wall-clock wait.
        seconds = catalog_seconds if path == "/v1/catalog" else 12
        if timeout < seconds:
            raise TimeoutError("archive aggregation exceeded request budget")
        value = bodies[path]
        return Response(
            value if isinstance(value, bytes) else json.dumps(value).encode(), request.full_url
        )

    stub_requests(scripts, monkeypatch, open_url)
    if catalog_seconds > 180:
        with pytest.raises(TimeoutError):
            scripts.manage.probe("https://fly.boboji.fyi")
        assert "/v1/itineraries/search" not in budgets
    else:
        assert scripts.manage.probe("https://fly.boboji.fyi")["route_search"] == "passed"
    assert budgets.pop("/v1/catalog") == 180
    assert set(budgets.values()) == {20}


def test_application_overlay_rejects_replaced_routing_code(tmp_path, scripts, monkeypatch):
    import hashlib

    import server

    installed = tmp_path / "packages"
    package = installed / "server"
    package.mkdir(parents=True)
    monkeypatch.setattr(server, "__file__", str(package / "__init__.py"))
    reader = package / "reader.py"
    reader.write_text("SQL routing")
    receipt = tmp_path / "installation.json"
    receipt.write_text(
        json.dumps(
            {
                "application_overlay": {
                    "files_sha256": {
                        "server/reader.py": hashlib.sha256(reader.read_bytes()).hexdigest()
                    }
                }
            }
        )
    )
    scripts.serve.verify_application_overlay(tmp_path)
    reader.write_text("old capped routing")
    with pytest.raises(ValueError, match="differs from its deployment receipt: server/reader.py"):
        scripts.serve.verify_application_overlay(tmp_path)
    reader.unlink()
    with pytest.raises(ValueError, match="differs from its deployment receipt"):
        scripts.serve.verify_application_overlay(tmp_path)


def test_installer_can_check_new_wheel_before_replacing_old_overlay_receipt(installed, scripts):
    (installed / "installation.json").write_text(
        json.dumps({"application_overlay": {"files_sha256": {"server/reader.py": "obsolete"}}})
    )
    (installed / "installing").write_text("Installation in progress")
    asyncio.run(scripts.serve.check_installation(installed))
    # Normal serving always verifies, even if an interrupted installer left its marker.
    with pytest.raises(ValueError, match="differs from its deployment receipt"):
        scripts.serve.create_server(installed)
    (installed / "installing").unlink()
    with pytest.raises(ValueError, match="differs from its deployment receipt"):
        asyncio.run(scripts.serve.check_installation(installed))


def test_installed_monitoring_wraps_outer_rejections_and_preflight_is_quiet(
    installed, scripts, tmp_path, monkeypatch
):
    from bobowatch.config import initialize

    from server.monitoring import provision

    initialize(tmp_path / "watch")
    provision(installed, tmp_path / "watch")
    app, _ = scripts.serve.create_server(installed)
    monkeypatch.setattr("bobowatch.adapter.Monitor.start", lambda self: None)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/", headers={"Host": "unrelated.invalid"}).status_code == 400
        assert client.post("/v1/plans/rebuild", content=b"x" * 128001).status_code == 413
        metrics = app.monitor.snapshot()
        assert metrics["requests"] == 3
        assert metrics["http"]["status_classes"][3] == 2
        assert metrics["http"]["in_flight"] == 0
    monkeypatch.setattr(
        "server.monitoring.wrap_application",
        lambda *args, **kwargs: pytest.fail("Installation checks must not start telemetry"),
    )
    asyncio.run(scripts.serve.check_installation(installed))


def test_deployment_wrapper_routes_help_without_starting_jobs(tmp_path):
    import os

    result = subprocess.run(
        [str(REPO / "deployment/flyji"), "deploy", "--help"],
        env=os.environ | {"FLYJI_RUNTIME_DIR": str(tmp_path / "not-installed")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--bobowatch-runtime-dir" in result.stdout
    assert not (tmp_path / "not-installed").exists()


def test_single_command_deploy_orders_stop_install_provision_start_and_check(
    tmp_path, scripts, monkeypatch
):
    spec = importlib.util.spec_from_file_location("flyji_deploy", REPO / "deployment/deploy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kw: calls.append("submodule"))
    monkeypatch.setattr(module, "loaded", lambda job: True)
    monkeypatch.setattr(module, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(module, "start", lambda root: calls.append("start"))
    monkeypatch.setattr(module, "check", lambda root: calls.append("check"))
    original = module.importlib.util.spec_from_file_location

    def specification(name, path):
        spec = original(name, path)

        def execute(installer):
            installer.install = lambda root, repo, watch_runtime=None: calls.append(
                ("install-and-provision", root, watch_runtime)
            )

        spec.loader.exec_module = execute
        return spec

    monkeypatch.setattr(module.importlib.util, "spec_from_file_location", specification)
    root, watch = tmp_path / "flight", tmp_path / "watch"
    module.deploy(root, watch)
    assert calls == ["submodule", "stop", ("install-and-provision", root, watch), "start", "check"]


@pytest.mark.parametrize("monitoring", [False, [], {"enabled": "true"}])
def test_bad_monitoring_settings_refuse_install_before_build(
    tmp_path, checkout, scripts, monkeypatch, monitoring
):
    root = runtime(tmp_path / "runtime", checkout / "var/hosted/flights.db")
    data = json.loads((root / "settings.json").read_text())
    data["monitoring"] = monitoring
    private_file(root / "settings.json", json.dumps(data).encode())
    monkeypatch.setattr(scripts.install, "loaded", lambda job: False)
    monkeypatch.setattr(
        scripts.install.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Invalid monitoring must precede build/install"),
    )
    with pytest.raises(ValueError, match="monitoring.enabled"):
        scripts.install.install(root, checkout)
    assert not (root / "installing").exists()
    assert not (checkout / "var/hosted").exists()
