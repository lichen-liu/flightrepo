"""Manual controls for Flyji's app and Cloudflare connector on this Mac."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from serve import read_settings

LABELS = {"server": "fyi.boboji.flyji.server", "tunnel": "fyi.boboji.flyji.tunnel"}
# Match startup and ongoing probes; real-archive aggregation needs headroom
# beyond ten seconds while every HTTP operation remains bounded.
HTTP_PROBE_TIMEOUT = 20


def runtime_root() -> Path:
    root = Path(os.environ.get("FLYJI_RUNTIME_DIR", "~/Library/Application Support/fyi.boboji.fly"))
    root = root.expanduser()
    if not root.is_absolute():
        raise ValueError("FLYJI_RUNTIME_DIR must be an absolute path.")
    return root


def require_private(path: Path, *, directory: bool = False):
    info = path.lstat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"{path.name} must belong to you and be accessible only by you.")


def private_write(path: Path, content: bytes):
    if path.exists() or path.is_symlink():
        require_private(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".flyji-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=20)


def target(job: str) -> str:
    return f"gui/{os.getuid()}/{LABELS[job]}"


def loaded(job: str) -> bool:
    return launchctl("print", target(job)).returncode == 0


def job_definitions(root: Path, cloudflared: str) -> dict:
    common = {
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ExitTimeOut": 15,
        "WorkingDirectory": str(root),
        "Umask": 0o077,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    }
    commands = {
        "server": [str(root / "venv/bin/python"), str(root / "serve.py")],
        "tunnel": [
            cloudflared,
            "tunnel",
            "--no-autoupdate",
            "--loglevel",
            "error",
            "--metrics",
            "127.0.0.1:0",
            "run",
            "--token-file",
            str(root / "tunnel-token"),
        ],
    }
    return {
        job: {
            **common,
            "Label": LABELS[job],
            "ProgramArguments": command,
            "StandardOutPath": str(root / "logs" / f"{job}.out.log"),
            "StandardErrorPath": str(root / "logs" / f"{job}.err.log"),
        }
        for job, command in commands.items()
    }


def wait_for_server(port: int):
    deadline = time.monotonic() + 60
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            # Real archives need longer than a demo to aggregate readiness. A short
            # read timeout leaves expensive requests running and floods startup.
            with urlopen(
                f"http://127.0.0.1:{port}/health/ready", timeout=min(HTTP_PROBE_TIMEOUT, remaining)
            ) as response:
                data = json.load(response)
                if response.status == 200 and data.get("status") == "ready":
                    return
        except (URLError, TimeoutError, OSError, ValueError):
            time.sleep(0.2)
    raise RuntimeError("The app did not become ready. Check the private logs.")


def stop():
    failures = []
    for job in ("tunnel", "server"):
        try:
            if loaded(job):
                result = launchctl("bootout", target(job))
                if result.returncode and loaded(job):
                    failures.append(job)
        except (OSError, subprocess.SubprocessError):
            failures.append(job)
    if failures:
        raise RuntimeError("Could not unload: " + ", ".join(failures))
    print("Stopped. Both jobs are unloaded and will not restart.")


def connector_binary() -> str:
    binary = shutil.which("cloudflared")
    if not binary:
        raise ValueError("Install the connector first: brew install cloudflared")
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
    match = re.search(r"\b(\d{4})\.(\d+)\.(\d+)\b", result.stdout)
    if result.returncode or not match or tuple(map(int, match.groups())) < (2025, 4, 0):
        raise ValueError("Update cloudflared to 2025.4.0 or later for token-file support.")
    return str(Path(binary).resolve())


def start(root: Path):
    state = {job: loaded(job) for job in LABELS}
    if all(state.values()):
        print("Already loaded. Use status and check --public to verify it.")
        return
    if any(state.values()):
        raise ValueError("Only one job is loaded. Run stop, then start.")
    require_private(root, directory=True)
    for directory in ("jobs", "logs", "venv"):
        require_private(root / directory, directory=True)
    if (root / "installing").exists() or (root / "installing").is_symlink():
        raise ValueError("Installation is incomplete. Rerun the installer while stopped.")
    for name in ("settings.json", "installation.json", "manage.py", "serve.py", "tunnel-token"):
        require_private(root / name)
    token = (root / "tunnel-token").read_text().strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError("Save only the tunnel token with the tunnel command first.")
    settings = read_settings(root)
    binary = connector_binary()
    checked = subprocess.run(
        [str(root / "venv/bin/python"), str(root / "serve.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if checked.returncode:
        raise RuntimeError("Installation check failed. Rerun the installer while stopped.")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", settings["port"]))
        except OSError as error:
            raise ValueError(
                "Port is occupied. Stop its app or change settings.json and the tunnel route."
            ) from error
    definitions = job_definitions(root, binary)
    try:
        for job in ("server", "tunnel"):
            path = root / "jobs" / f"{LABELS[job]}.plist"
            private_write(path, plistlib.dumps(definitions[job]))
            result = launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
            if result.returncode:
                raise RuntimeError(f"Could not load {job}. Check the private logs.")
            if job == "server":
                wait_for_server(settings["port"])
    except BaseException as error:
        try:
            stop()
        except RuntimeError as cleanup_error:
            raise RuntimeError(f"Startup failed; cleanup incomplete: {cleanup_error}") from error
        raise
    print(f"Started locally at http://127.0.0.1:{settings['port']}/")
    print(
        f"Tunnel is starting for https://{settings['hostname']}/. Run check --public to verify it."
    )


def save_token(root: Path):
    require_private(root, directory=True)
    if any(loaded(job) for job in LABELS):
        raise ValueError("Stop Flyji before changing its tunnel token.")
    if not sys.stdin.isatty():
        raise ValueError("Run the tunnel command in a terminal to keep the token hidden.")
    token = getpass.getpass("Cloudflare tunnel token (hidden): ").strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError("Paste only the token, not the dashboard's command.")
    private_write(root / "tunnel-token", (token + "\n").encode())
    print("Token saved privately. Nothing was started.")


def process_name(pid: int) -> str:
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return (
        Path(result.stdout.strip()).name
        if result.returncode == 0 and result.stdout.strip()
        else "unavailable"
    )


def status(root: Path):
    settings = read_settings(root)
    print(f"Local app: http://127.0.0.1:{settings['port']}/ (configured port: {settings['port']})")
    for job in LABELS:
        result = launchctl("print", target(job))
        if result.returncode:
            print(f"{job}: stopped (not loaded); PID: none; process: none")
            continue
        state = re.search(r"(?m)^\tstate = (.+)$", result.stdout)
        pid = re.search(r"(?m)^\tpid = ([1-9][0-9]*)$", result.stdout)
        name = process_name(int(pid[1])) if pid else "none"
        print(
            f"{job}: {state[1] if state else 'loaded'}; PID: {pid[1] if pid else 'none'}; process: {name}"
        )
    print(f"Private logs: {root / 'logs'}")
    print("Process state does not verify the public URL. Use check --public.")


def probe(base_url: str, *, data_mode="synthetic") -> dict:
    """Verify the served app and a real synthetic route, with normal TLS validation."""

    def origin(url):
        parts = urlsplit(url)
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)

    expected_origin = origin(base_url)

    class SameOriginRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, new_url):
            if origin(new_url) != expected_origin:
                raise ValueError("Flyji redirected away from the configured origin.")
            return super().redirect_request(request, fp, code, msg, headers, new_url)

    opener = build_opener(SameOriginRedirect())

    def fetch(path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            base_url + path,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Flyji-Deployment-Check/1.0",
            },
        )
        with opener.open(request, timeout=HTTP_PROBE_TIMEOUT) as response:
            if origin(response.geturl()) != expected_origin:
                raise ValueError("The response is not from the configured Flyji origin.")
            content = response.read(1_000_001)
            if response.status != 200 or len(content) > 1_000_000:
                raise ValueError("Unexpected or oversized response from Flyji.")
            return content

    if b"<title>Flyji" not in fetch("/"):
        raise ValueError("The URL is not serving the Flyji workspace.")
    if b"via_airport" not in fetch("/assets/app.mjs"):
        raise ValueError("The Flyji frontend is missing or outdated.")
    ready = json.loads(fetch("/health/ready"))
    catalog = json.loads(fetch("/v1/catalog"))
    if not isinstance(ready, dict) or not isinstance(catalog, dict):
        raise ValueError("Unexpected readiness or catalog response from Flyji.")
    dataset = catalog.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("The Flyji catalog is missing its dataset.")
    count = dataset.get("flight_count")
    departure = dataset.get("first_departure")
    if data_mode == "real":
        if (
            ready.get("status") != "ready"
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or dataset.get("data_mode") != "real"
        ):
            raise ValueError("Real serving requires a ready database without demo records")
        if count == 0:
            return {"url": base_url, "flight_count": 0, "route_search": "empty dataset"}
        if not any(dataset.get("time_bases", {}).values()):
            return {
                "url": base_url,
                "flight_count": count,
                "route_search": "no complete time evidence",
            }
        route = catalog.get("suggested_route")
        if not isinstance(route, dict) or not isinstance(departure, str):
            raise ValueError("Real dataset is missing searchable departure evidence")
        first = datetime.fromisoformat(departure.replace("Z", "+00:00"))
        start = first.replace(hour=0, minute=0, second=0, microsecond=0)
        basis = dataset.get("default_time_basis", "scheduled")
        result = json.loads(
            fetch(
                "/v1/itineraries/search",
                {
                    "origin": route.get("origin_iata"),
                    "destination": route.get("destination_iata"),
                    "departure_start": start.isoformat(),
                    "departure_end": (start + timedelta(days=2)).isoformat(),
                    "horizon_end": (start + timedelta(days=7)).isoformat(),
                    "time_basis": basis,
                },
            )
        )
        if not isinstance(result, dict) or not result.get("items"):
            raise ValueError("Real dataset route search returned no itinerary")
        legs = result["items"][0].get("legs", [])
        if (
            not legs
            or legs[0].get("origin_iata") != route.get("origin_iata")
            or legs[-1].get("destination_iata") != route.get("destination_iata")
        ):
            raise ValueError("Real dataset route search returned the wrong route")
        return {"url": base_url, "flight_count": count, "route_search": "passed"}
    if (
        ready.get("status") != "ready"
        or dataset.get("data_mode") != "synthetic"
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
        or not isinstance(departure, str)
    ):
        raise ValueError("The first hosted version must use synthetic flight data.")
    first = datetime.fromisoformat(departure.replace("Z", "+00:00"))
    start = first.replace(hour=0, minute=0, second=0, microsecond=0)
    result = json.loads(
        fetch(
            "/v1/itineraries/search",
            {
                "origin": "YYZ",
                "destination": "CDG",
                "via_airport": "AMS",
                "max_legs": 3,
                "departure_start": start.isoformat(),
                "departure_end": (start + timedelta(days=1)).isoformat(),
                "horizon_end": (start + timedelta(days=7)).isoformat(),
            },
        )
    )
    items = result.get("items") if isinstance(result, dict) else None
    legs = (
        items[0].get("legs")
        if isinstance(items, list) and items and isinstance(items[0], dict)
        else None
    )
    if (
        not isinstance(legs, list)
        or not legs
        or any(not isinstance(leg, dict) for leg in legs)
        or legs[0].get("origin_iata") != "YYZ"
        or legs[-1].get("destination_iata") != "CDG"
        or not any(leg.get("destination_iata") == "AMS" for leg in legs[:-1])
    ):
        raise ValueError("The synthetic YYZ → AMS → CDG search did not return a route.")
    return {"url": base_url, "flight_count": count, "route_search": "passed"}


def check(root: Path, *, public: bool = False):
    require_private(root, directory=True)
    require_private(root / "settings.json")
    settings = read_settings(root)
    base = f"https://{settings['hostname']}" if public else f"http://127.0.0.1:{settings['port']}"
    result = probe(base, data_mode=settings["data_mode"])
    print(
        f"Verified {'live' if public else 'locally'}: {base}/ · {settings['data_mode']} data · {result['flight_count']} flights"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manually control Flyji on this Mac.")
    parser.add_argument("command", choices=["start", "stop", "status", "tunnel", "check"])
    parser.add_argument("--public", action="store_true", help="verify the configured HTTPS domain")
    parser.add_argument("--runtime-dir", type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.getuid() == 0:
        parser.error("Run on macOS as your normal user, without sudo.")
    if args.public and args.command != "check":
        parser.error("--public is only used with check.")
    os.umask(0o077)
    try:
        root = args.runtime_dir.expanduser() if args.runtime_dir else runtime_root()
        if not root.is_absolute():
            raise ValueError("The runtime directory must be an absolute path.")
        {
            "start": lambda: start(root),
            "stop": stop,
            "status": lambda: status(root),
            "tunnel": lambda: save_token(root),
            "check": lambda: check(root, public=args.public),
        }[args.command]()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
