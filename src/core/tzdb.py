"""Use the packaged IANA rules consistently, independent of host TZPATH."""

from functools import lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzdata

VERSION = "2026.2"


@lru_cache(maxsize=1024)
def zone(name):
    if tzdata.__version__ != VERSION:
        raise RuntimeError(f"Install the pinned tzdata=={VERSION} dependency")
    parts = name.split("/")
    if any(part in ("", ".", "..") or "\\" in part for part in parts):
        raise ZoneInfoNotFoundError(name)
    try:
        with files("tzdata.zoneinfo").joinpath(*parts).open("rb") as handle:
            return ZoneInfo.from_file(handle, key=name)
    except (FileNotFoundError, IsADirectoryError, ValueError) as error:
        raise ZoneInfoNotFoundError(name) from error
