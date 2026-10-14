"""Reduce daily readsb traces to explicitly filtered aircraft-day observations."""

import gzip
import hashlib
import io
import json
import math
import re
import tarfile
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime, time

from pydantic import BaseModel, ConfigDict, Field

from core.models import AircraftMetadata, AircraftObservation

MAX_TRACE_BYTES = 32 * 1024**2


class CommercialFilter(BaseModel):
    """Explicit selection evidence, never a claim that a trace is a scheduled flight."""

    model_config = ConfigDict(extra="forbid")
    fixed_wing_types: set[str] = Field(min_length=1)
    callsign_prefixes: set[str] = Field(default_factory=set)
    owner_operators: set[str] = Field(default_factory=set)


def summarize_trace(data, day, rules):
    hex_id = data.get("icao", "")
    if not isinstance(hex_id, str) or not re.fullmatch(r"[0-9a-f]{6}", hex_id):
        return None, "unidentified_address"
    if data.get("t") not in rules.fixed_wing_types:
        return None, "type_excluded_or_unknown"
    flags = data.get("dbFlags")
    if isinstance(flags, int) and flags & 1:
        return None, "military"
    start = datetime.combine(day, time.min, UTC).timestamp()
    points = [row for row in data["trace"] if start <= data["timestamp"] + row[0] < start + 86400]
    if not points:
        return None, "no_positions_in_day"
    extras = [row[8] for row in points if len(row) > 8 and isinstance(row[8], dict)]
    callsigns = sorted(
        {extra["flight"].strip() for extra in extras if extra.get("flight", "").strip()}
    )
    prefixes = sorted(
        {
            prefix
            for prefix in rules.callsign_prefixes
            for call in callsigns
            if re.fullmatch(re.escape(prefix) + r"[0-9][A-Z0-9]*", call)
        }
    )
    evidence = [f"callsign_prefix:{prefix}" for prefix in prefixes]
    if data.get("ownOp") in rules.owner_operators:
        evidence.append("owner_operator:" + data["ownOp"])
    if not evidence:
        return None, "commercial_evidence_missing"
    categories = {extra["category"] for extra in extras if extra.get("category")}
    if categories & {"A7", "B1", "B2", "B3", "B4", "B6", "B7", "C1", "C2", "C3"}:
        return None, "non_fixed_wing_category"

    def maximum(index):
        values = [
            row[index]
            for row in points
            if isinstance(row[index], (float, int)) and math.isfinite(row[index])
        ]
        return max(values) if values else None

    times = [data["timestamp"] + row[0] for row in points]
    observed = datetime.fromtimestamp(max(times), UTC)
    aircraft = AircraftMetadata(
        icao_hex=hex_id,
        registration=data.get("r"),
        type_code=data.get("t"),
        description=data.get("desc"),
        owner_operator=data.get("ownOp"),
        manufacture_year=str(data["year"]) if data.get("year") is not None else None,
        emitter_category=next(iter(sorted(categories)), None),
        military=bool(flags & 1) if isinstance(flags, int) else None,
        source="adsb.lol",
        observed_at=observed,
    )
    return AircraftObservation(
        day=day,
        aircraft=aircraft,
        first_seen=datetime.fromtimestamp(min(times), UTC),
        last_seen=observed,
        positions=len(points),
        callsigns=callsigns,
        data_sources=sorted({row[9] for row in points if len(row) > 9 and row[9]}),
        squawks=sorted({extra["squawk"] for extra in extras if extra.get("squawk")}),
        emergencies=sorted(
            {extra["emergency"] for extra in extras if extra.get("emergency") not in (None, "none")}
        ),
        max_altitude_ft=maximum(3),
        max_ground_speed_knots=maximum(4),
        classification_evidence=evidence,
    ), "retained"


class JoinedFiles:
    """Read split tar parts as one byte stream without extracting to disk."""

    def __init__(self, files):
        self.files = iter(files)
        self.current = next(self.files, None)

    def read(self, size):
        chunks = []
        while size and self.current is not None:
            chunk = self.current.read(size)
            if not chunk:
                self.current = next(self.files, None)
                continue
            chunks.append(chunk)
            size -= len(chunk)
        return b"".join(chunks)


def summarize_archives(paths, day, rules):
    """Do not extract members; metadata-only catalogues omit heatmaps and all tracks."""
    stats, observations = Counter(), []
    with ExitStack() as stack:
        files = [stack.enter_context(path.open("rb")) for path in paths]
        archive = stack.enter_context(tarfile.open(fileobj=JoinedFiles(files), mode="r|"))
        for member in archive:
            if not member.isfile() or not re.fullmatch(
                r"(?:\./)?traces/[0-9a-f]{2}/trace_full_[~0-9a-f]+\.json", member.name
            ):
                continue
            stats["traces"] += 1
            if member.size > MAX_TRACE_BYTES:
                raise ValueError("Trace exceeds compressed size limit; day not committed")
            with archive.extractfile(member) as source:
                compressed = source.read()
            stats["compressed_trace_bytes"] += len(compressed)
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
                raw = source.read(MAX_TRACE_BYTES + 1)
            if len(raw) > MAX_TRACE_BYTES:
                raise ValueError("Trace exceeds decompressed size limit; day not committed")
            observation, reason = summarize_trace(json.loads(raw), day, rules)
            stats[reason] += 1
            if observation is not None:
                observation.trace_offset = member.offset_data
                observation.trace_bytes = member.size
                observation.trace_sha256 = hashlib.sha256(compressed).hexdigest()
                stats["retained_compressed_trace_bytes"] += len(compressed)
                stats["positions"] += observation.positions
                observations.append(observation)
    stats["summary_json_bytes"] = sum(len(o.model_dump_json().encode()) for o in observations)
    return observations, dict(stats)
