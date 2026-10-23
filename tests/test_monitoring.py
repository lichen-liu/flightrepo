"""Deployment registration and actual HTTP telemetry without installed user jobs."""

import importlib
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from bobowatch.config import initialize
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.monitoring import provision, wrap_application


@pytest.fixture(scope="session")
def watch_backend():
    # Editable installs intentionally expose only the bundled adapter. Load the
    # full test receiver in a separate namespace from the pinned source checkout.
    source = Path(__file__).resolve().parents[1] / "vendor/bobowatch/bobowatch"
    name = "_watch_receiver_tests"
    spec = importlib.util.spec_from_file_location(
        name, source / "__init__.py", submodule_search_locations=[str(source)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return SimpleNamespace(
        Application=importlib.import_module(name + ".application").Application,
        SQLiteStore=importlib.import_module(name + ".storage").SQLiteStore,
    )


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def flight_runtime(path):
    path.mkdir(mode=0o700)
    settings = path / "settings.json"
    settings.write_text(
        json.dumps(
            {"hostname": "fly.boboji.fyi", "port": 8014, "database_path": str(path / "flights.db")}
        )
    )
    settings.chmod(0o600)
    return settings


def test_registration_reuses_token_and_preserves_other_settings(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    initialize(watch, port=9123)
    settings = flight_runtime(tmp_path / "flight")
    config = provision(settings.parent, watch)
    before = settings.read_bytes()
    token = Path(config["token_file"]).read_bytes()
    assert config["endpoint"] == "http://127.0.0.1:9123/v1/ingest"
    monkeypatch.delenv("BOBOWATCH_RUNTIME_DIR", raising=False)
    assert provision(settings.parent) == config
    assert settings.read_bytes() == before
    assert Path(config["token_file"]).read_bytes() == token
    assert json.loads(before)["database_path"] == str(settings.parent / "flights.db")
    assert not (settings.parent / "flights.db").exists()
    assert not (watch / "monitor.sqlite3").exists()
    assert token.decode().strip() not in before.decode()
    assert settings.stat().st_mode & 0o077 == 0


def test_disabled_app_has_no_monitoring_side_effects(tmp_path):
    settings = flight_runtime(tmp_path / "flight")
    app = FastAPI()
    with patch("bobowatch.adapter.Monitor.start", side_effect=AssertionError("No sender")):
        assert wrap_application(app, settings) is app


def test_real_http_delivery_recovers_after_monitoring_server_stops(tmp_path, watch_backend):
    server = make_server("127.0.0.1", 0, lambda e, s: [], handler_class=QuietHandler)
    port = server.server_port
    config = initialize(tmp_path / "watch", port=port)
    store = watch_backend.SQLiteStore(config)
    application = watch_backend.Application(config, store)
    server.set_app(application)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = flight_runtime(tmp_path / "flight")
    provision(settings.parent, config.path.parent)
    application.reload()
    app = FastAPI()

    @app.get("/items/{item}")
    def item(item: str):
        return {"item": item}

    wrapped = wrap_application(app, settings, routes=("/items/{item}",))
    try:
        with TestClient(wrapped) as client:
            assert client.get("/items/one").status_code == 200
            monitor = wrapped.monitor
            run = monitor.run_id
            time.sleep(0.01)
            assert monitor.flush()
            token = Path(monitor.token_file).read_text().strip()
            assert token not in json.dumps(store.overview({}, 900))
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            assert client.get("/items/two").status_code == 200
            assert not monitor.flush()
            server = make_server("127.0.0.1", port, application, handler_class=QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            time.sleep(0.01)
            assert monitor.flush()
            assert monitor.run_id == run
            data = store.overview({}, 900)
            assert sum(site["requests"] for site in data["sites"]) == 2
            assert monitor.snapshot()["http"]["routes"][0]["route"] == "/items/{item}"
            assert monitor.snapshot()["http"]["in_flight"] == 0
        assert wrapped.monitor is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


def test_enabled_invalid_settings_fail_before_listening(tmp_path):
    settings = flight_runtime(tmp_path / "flight")
    data = json.loads(settings.read_text())
    data["monitoring"] = {
        "enabled": True,
        "replica": "flight-r01",
        "endpoint": "http://remote.invalid/v1/ingest",
        "token_file": "x",
    }
    settings.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="loopback"):
        wrap_application(FastAPI(), settings)
