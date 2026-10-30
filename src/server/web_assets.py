"""Serve content-versioned browser assets and retain one previous release."""

import hashlib
import mimetypes
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.staticfiles import NotModifiedResponse
from starlette.types import Scope


@dataclass(frozen=True)
class AssetRelease:
    version: str
    files: dict[str, bytes]


def _snapshot(root: Path, names: list[str]) -> AssetRelease:
    files = {name: (root / name).read_bytes() for name in names}
    digest = hashlib.sha256()
    for name, content in files.items():
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return AssetRelease(digest.hexdigest()[:16], files)


class WebAssets(StaticFiles):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._lock = Lock()
        self._releases: OrderedDict[str, AssetRelease] = OrderedDict()
        self._fingerprint: tuple = ()
        self._current: AssetRelease | None = None
        super().__init__(directory=self.root)

    def release(self) -> AssetRelease:
        with self._lock:
            fingerprint = []
            for path in sorted(self.root.rglob("*")):
                if path.is_file():
                    stat = path.stat()
                    fingerprint.append(
                        (
                            str(path.relative_to(self.root)),
                            stat.st_ino,
                            stat.st_size,
                            stat.st_mtime_ns,
                            stat.st_ctime_ns,
                        )
                    )
            if self._current is not None and tuple(fingerprint) == self._fingerprint:
                return self._current
            release = _snapshot(self.root, [name for name, *_ in fingerprint])
            self._fingerprint = tuple(fingerprint)
            self._current = release
            self._releases[release.version] = release
            self._releases.move_to_end(release.version)
            while len(self._releases) > 2:
                self._releases.popitem(last=False)
            return release

    def page(self) -> tuple[str, str]:
        release = self.release()
        html = release.files["index.html"].decode("utf-8")
        return html.replace("/assets/", f"/assets/v-{release.version}/"), release.version

    async def get_response(self, path: str, scope: Scope) -> Response:
        first, _, rest = path.partition("/")
        if not first.startswith("v-"):
            return await super().get_response(path, scope)
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=405)
        if not re.fullmatch(r"v-[a-f0-9]{16}", first):
            raise HTTPException(status_code=404)
        version = first.removeprefix("v-")
        with self._lock:
            release = self._releases.get(version)
        # A known content-addressed release is immutable. Page requests detect
        # edits; avoid walking and stat-ing the asset tree for each dependency.
        # A cold worker still needs to discover the release named by the page.
        if release is None:
            await run_in_threadpool(self.release)
            with self._lock:
                release = self._releases.get(version)
        if release is None or rest not in release.files:
            raise HTTPException(status_code=404)
        response = Response(
            release.files[rest],
            media_type=mimetypes.guess_type(rest)[0] or "application/octet-stream",
            headers={
                "ETag": f'"{release.version}"',
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response
