"""Optional BoboWatch instrumentation and same-user deployment provisioning."""

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path


def wrap_application(app, settings_path, *, routes=(), revision="unknown"):
    """Disabled by default; importing Flightrepo starts no monitoring work."""
    settings_path = Path(settings_path)
    data = json.loads(settings_path.read_text())
    config = data.get("monitoring", {"enabled": False})
    if not isinstance(config, dict) or type(config.get("enabled", False)) is not bool:
        raise ValueError("monitoring.enabled must be a boolean")
    if not config.get("enabled", False):
        return app
    from bobowatch.asgi import InstrumentedApp
    from bobowatch.client import ConfiguredMonitor, client_settings

    client_settings(settings_path)  # Reject malformed enabled settings before listening.
    return InstrumentedApp(
        app, lambda: ConfiguredMonitor(settings_path, routes=routes, revision=revision)
    )


def provision(root, watch_runtime=None):
    """An idempotent deployment step; no BoboWatch process or database is started."""
    from bobowatch.paths import runtime_root
    from bobowatch.provision import ensure_registration, private_write, require_private

    root = Path(root).expanduser().resolve()
    require_private(root, directory=True)
    path = root / "settings.json"
    require_private(path)
    data = json.loads(path.read_text())
    registration = data.get("monitoring_registration", {})
    if not isinstance(registration, dict):
        raise ValueError("monitoring_registration must be an object")
    selected = (
        watch_runtime or os.environ.get("BOBOWATCH_RUNTIME_DIR") or registration.get("runtime_dir")
    )
    watch = runtime_root(selected)
    host = registration.get(
        "host", "host-" + hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]
    )
    identity = {
        "project": registration.get("project", "flightrepo"),
        "site": registration.get("site", "flyji-production"),
        "host": host,
        "replica": registration.get("replica", "flyji-" + host + "-r01"),
        "environment": registration.get("environment", "production"),
    }
    tokens = root / "tokens"
    tokens.mkdir(mode=0o700, exist_ok=True)
    require_private(tokens, directory=True)
    config = ensure_registration(runtime=watch, token_file=tokens / "bobowatch.token", **identity)
    data["monitoring"] = config
    data["monitoring_registration"] = {"runtime_dir": str(watch), **identity}
    private_write(path, (json.dumps(data, indent=2) + "\n").encode())
    return config


def main():
    parser = argparse.ArgumentParser(description="Provision optional Flightrepo monitoring.")
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--bobowatch-runtime-dir", type=Path)
    args = parser.parse_args()
    result = provision(args.runtime_dir, args.bobowatch_runtime_dir)
    print(json.dumps(result))  # Contains file paths, never credentials.


if __name__ == "__main__":
    main()
