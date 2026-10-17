"""Version the whole browser module tree so CDN caching cannot mix releases."""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles


@lru_cache(maxsize=4)
def _revision(root, fingerprint):
    digest = hashlib.sha256()
    for name, *_ in fingerprint:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update((root / name).read_bytes())
    return digest.hexdigest()[:16]


class WebAssets(StaticFiles):
    def __init__(self, root: Path):
        self.root = root.resolve()
        super().__init__(directory=self.root)

    def revision(self):
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
        return _revision(self.root, tuple(fingerprint))

    def page(self):
        revision = self.revision()
        html = (self.root / "index.html").read_text()
        return html.replace("/assets/", f"/assets/v-{revision}/"), revision

    async def get_response(self, path, scope):
        first, _, rest = path.partition("/")
        if first.startswith("v-"):
            if not re.fullmatch(r"v-[a-f0-9]{16}", first) or first != f"v-{self.revision()}":
                raise HTTPException(status_code=404)
            path = rest
        return await super().get_response(path, scope)
