"""Cohort review keeps acknowledgement, manual verification and rejection distinct."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from core.database_roles import require_ingestion
from core.file_lock import file_mutex
from core.models import FlightRecord
from core.quality import describe_quality
from core.storage import SQLiteConnection, flight_record, source_id, store_flight, utc_us
from ingestion.audit import KNOWN_REASONS, assessment, scope_sql
from ingestion.store import IngestionStore

VERSION = "itinerary-cohort-review-v1"
RESULTS = {"exact", "adjacent", "different", "incomplete", "missing", "not_checked"}


def reference_result_sql():
    return """CASE WHEN json_extract(f.route_verification,'$.source')='vrs'
        AND json_extract(f.route_verification,'$.callsign') IS nullif(upper(trim(f.callsign)),'')
        AND json_extract(f.route_verification,'$.origin_iata')=f.origin_iata
        AND json_extract(f.route_verification,'$.destination_iata')=f.destination_iata
        THEN json_extract(f.route_verification,'$.match_kind') ELSE 'not_checked' END"""


class Fingerprint:
    def __init__(self):
        self.rows = []

    def step(self, *values):
        self.rows.append(
            hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).digest()
        )

    def finalize(self):
        return hashlib.sha256(b"".join(sorted(self.rows))).hexdigest()


JOINS = """FROM flight_legs f JOIN data_sources s ON s.id=f.source_id
    JOIN airports a ON a.iata=f.origin_iata JOIN airports b ON b.iata=f.destination_iata"""
DIGEST = """review_fingerprint(s.code,f.source_record_key,f.revision,
    a.name,a.timezone,a.latitude,a.longitude,b.name,b.timezone,b.latitude,b.longitude)"""


def normalized(selection):
    allowed = {
        "source",
        "start",
        "end",
        "origin",
        "destination",
        "reason",
        "reference_result",
        "record_key",
        "reasons_exact",
        "evidence_kind",
        "unreviewed_only",
    }
    if not isinstance(selection, dict) or set(selection) - allowed:
        raise ValueError("Use a review selection object, not an entire queue report")
    value = dict(source="adsblol", unreviewed_only=False) | selection
    if value.get("reference_result") is not None and value["reference_result"] not in RESULTS:
        raise ValueError("Unknown reference result")
    if value.get("reasons_exact") is not None and (
        not isinstance(value["reasons_exact"], list) or set(value["reasons_exact"]) - KNOWN_REASONS
    ):
        raise ValueError("Unknown review reasons")
    # Reuse the actual UTC date/code/reason validation and bind all user values.
    where(value)
    return value


def where(selection):
    clause, values = scope_sql(
        selection.get("start"),
        selection.get("end"),
        selection.get("origin"),
        selection.get("destination"),
        selection.get("reason"),
    )
    clause += " AND s.code=?"
    values.append(selection.get("source", "adsblol"))
    if selection.get("unreviewed_only"):
        clause += " AND f.itinerary_reviewed=0"
    for key, expression in [
        ("record_key", "f.source_record_key"),
        ("reference_result", reference_result_sql()),
        ("evidence_kind", "f.evidence_kind"),
    ]:
        if selection.get(key) is not None:
            clause += " AND (" + expression + ")=?"
            values.append(selection[key])
    if selection.get("reasons_exact") is not None:
        clause += " AND json(f.review_reasons)=json(?)"
        values.append(json.dumps(selection["reasons_exact"]))
    return clause, values


def prepare(connection):
    require_ingestion(connection)
    connection.create_aggregate("review_fingerprint", -1, Fingerprint)


def token(selection, fingerprint):
    return hashlib.sha256(
        json.dumps([selection, fingerprint], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def cohort_state(connection, selection):
    clause, values = where(selection)
    return connection.execute(
        f"SELECT count(*) records,{DIGEST} fingerprint {JOINS} WHERE {clause}", values
    ).fetchone()


def samples(connection, selection, limit):
    clause, values = where(selection)
    # Show different aircraft/reference-chain combinations instead of adjacent rows.
    rows = connection.execute(
        f"""SELECT s.code provider,f.source_record_key,
        MIN(f.id) sample_id,COUNT(*) occurrences {JOINS} WHERE {clause}
        GROUP BY f.aircraft_type_code,json_extract(f.route_verification,'$.reference_airports')
        ORDER BY occurrences DESC,sample_id LIMIT ?""",
        [*values, limit],
    ).fetchall()
    result = []
    for row in rows:
        raw = connection.execute(
            "SELECT * FROM flights WHERE provider=? AND provider_id=?",
            (row["provider"], row["source_record_key"]),
        ).fetchone()
        record = flight_record(raw)
        data = connection.execute(
            """SELECT f.*,s.code provider,a.latitude origin_lat,a.longitude origin_lon,
            b.latitude destination_lat,b.longitude destination_lon """
            + JOINS
            + " WHERE f.id=?",
            (row["sample_id"],),
        ).fetchone()
        result.append(
            dict(
                record=record.model_dump(mode="json"),
                evidence=assessment(data)[4],
                similar_records=row["occurrences"],
            )
        )
    return result


def queue(path, *, start=None, end=None, limit=20, include_acknowledged=False):
    if not 1 <= limit <= 100:
        raise ValueError("Choose a cohort limit between 1 and 100")
    base = normalized(
        dict(
            source="adsblol",
            start=str(start) if start else None,
            end=str(end) if end else None,
            unreviewed_only=True,
        )
    )
    clause, values = where(base)
    cohorts, levels, pending = [], {}, 0
    with SQLiteConnection(path, read_only=True).connect() as c:
        c.execute("BEGIN")
        prepare(c)
        acknowledged = {
            r[0]
            for r in c.execute("""SELECT d.source_record_key FROM ingestion_documents d
            JOIN data_sources s ON s.id=d.source_id WHERE s.code='review' AND d.kind='validation'
            AND json_extract(d.payload_json,'$.action')='acknowledge'""")
        }
        query = f"""SELECT f.origin_iata,f.destination_iata,f.review_reasons,f.evidence_kind,
            {reference_result_sql()} reference_result,COUNT(*) records,{DIGEST} fingerprint
            {JOINS} WHERE {clause} GROUP BY f.origin_iata,f.destination_iata,f.review_reasons,
            f.evidence_kind,reference_result"""
        for row in c.execute(query, values):
            reasons = json.loads(row["review_reasons"])
            quality = describe_quality(
                reasons=reasons,
                evidence_kind=row["evidence_kind"],
                reference_result=None
                if row["reference_result"] == "not_checked"
                else row["reference_result"],
            )
            level = quality["level"]
            levels[level] = levels.get(level, 0) + row["records"]
            if level not in {"uncertain", "conflict"}:
                continue
            selection = base | dict(
                origin=row["origin_iata"],
                destination=row["destination_iata"],
                reasons_exact=reasons,
                evidence_kind=row["evidence_kind"],
                reference_result=row["reference_result"],
            )
            key = token(selection, row["fingerprint"])
            done = key in acknowledged
            if not done:
                pending += row["records"]
            if done and not include_acknowledged:
                continue
            cohorts.append(
                dict(
                    selection=selection,
                    fingerprint=row["fingerprint"],
                    records=row["records"],
                    quality=quality,
                    acknowledged=done,
                )
            )
            cohorts.sort(
                key=lambda x: (x["quality"]["level"] != "conflict", -x["records"], x["fingerprint"])
            )
            del cohorts[limit:]
        for cohort in cohorts:
            cohort["samples"] = samples(c, cohort["selection"], 3)
    return dict(
        version=VERSION,
        scope=base,
        levels=levels,
        pending_records=pending,
        cohorts=cohorts,
        note="Acknowledgement records review workflow; it does not verify facts or erase confidence labels.",
    )


def show(path, selection, *, sample_limit=5):
    if not 1 <= sample_limit <= 20:
        raise ValueError("Choose between 1 and 20 samples")
    selection = normalized(selection)
    with SQLiteConnection(path, read_only=True).connect() as c:
        c.execute("BEGIN")
        prepare(c)
        state = cohort_state(c, selection)
        return dict(
            version=VERSION,
            selection=selection,
            records=state["records"],
            fingerprint=state["fingerprint"],
            samples=samples(c, selection, sample_limit),
        )


def decide(path, case, *, action, note, replacement=None):
    """Named producer operation. Groups may be acknowledged; approval/correction/rejection is single-record only."""
    if action not in {"acknowledge", "approve", "reject", "correct"} or not note.strip():
        raise ValueError("Choose a review action and provide a nonempty note")
    selection = normalized(case["selection"])
    store = IngestionStore(path)
    with file_mutex(Path(str(Path(path)) + ".job.lock"), blocking=False) as acquired:
        if not acquired:
            raise ValueError("Stop the active ingestion job before review decisions")
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            prepare(c)
            current = cohort_state(c, selection)
            if (
                current["records"] != case["records"]
                or current["fingerprint"] != case["fingerprint"]
            ):
                raise ValueError("Review selection changed; inspect a fresh case before deciding")
            if not current["records"]:
                raise ValueError("No records in this review case")
            before = after = None
            if action != "acknowledge":
                if current["records"] != 1:
                    raise ValueError(
                        "Only acknowledgement is allowed for a cohort; approval/correction/rejection requires one record"
                    )
                clause, values = where(selection)
                identity = c.execute(
                    f"SELECT f.id,f.source_id,f.source_record_key,s.code provider,f.locally_modified_us {JOINS} WHERE {clause}",
                    values,
                ).fetchone()
                raw = c.execute(
                    "SELECT * FROM flights WHERE provider=? AND provider_id=?",
                    (identity["provider"], identity["source_record_key"]),
                ).fetchone()
                original = flight_record(raw)
                before = original.model_dump(mode="json")
                if action == "reject":
                    if original.itinerary_reviewed or identity["locally_modified_us"] is not None:
                        raise ValueError(
                            "Protected manual record; use serving admin to review its existing decision"
                        )
                    c.execute(
                        "INSERT OR REPLACE INTO flight_tombstones VALUES(?,?,?)",
                        (
                            identity["source_id"],
                            identity["source_record_key"],
                            utc_us(datetime.now(UTC)),
                        ),
                    )
                    # Keep the source row for evidence; the shared view excludes its tombstone.
                else:
                    if action == "approve":
                        if original.itinerary_reviewed:
                            raise ValueError("Already manually reviewed")
                        updated = original.model_copy(update={"itinerary_reviewed": True})
                    else:
                        updated = FlightRecord.model_validate(replacement)
                        if (updated.provider, updated.provider_id) != (
                            original.provider,
                            original.provider_id,
                        ):
                            raise ValueError("Correction cannot change source identity")
                        updated.itinerary_reviewed = original.itinerary_reviewed
                        if updated.current_route_reference is None:
                            updated.route_verification = None
                    store_flight(c, updated, local_edit=True)
                    after = updated.model_dump(mode="json")
            receipt = dict(
                version=VERSION,
                action=action,
                selection=selection,
                fingerprint=current["fingerprint"],
                records=current["records"],
                note=note.strip(),
                decided_at=datetime.now(UTC).isoformat(),
                before=before,
                after=after,
            )
            payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            if len(payload.encode()) > 65536:
                raise ValueError("Review receipt exceeds 64 KiB")
            sid = source_id(c, "review")
            key = (
                token(selection, current["fingerprint"])
                if action == "acknowledge"
                else token(selection, current["fingerprint"]) + ":" + action
            )
            c.execute(
                """INSERT INTO ingestion_documents VALUES(?, 'validation', ?, 'manual-review',
                'local://itinerary-review', ?, NULL, NULL, ?, ?, 1)
                ON CONFLICT(source_id,kind,source_record_key) DO UPDATE SET fetched_us=excluded.fetched_us,
                content_sha256=excluded.content_sha256,payload_json=excluded.payload_json,
                revision=ingestion_documents.revision+1""",
                (
                    sid,
                    key,
                    utc_us(datetime.now(UTC)),
                    hashlib.sha256(payload.encode()).hexdigest(),
                    payload,
                ),
            )
    return dict(
        receipt,
        serving_changed=False,
        publication_required=action in {"approve", "reject", "correct"},
    )


def configure_commands(commands, *, writes=False, reports=True):
    """Admin shares only the read parser; producer alone receives decision commands."""
    from datetime import date

    review = commands.add_parser("review", help="rank uncertainty cohorts and inspect evidence")
    actions = review.add_subparsers(dest="review_action", required=True)
    listing = actions.add_parser(
        "queue", help="largest uncertain/conflicting cohorts, conflicts first"
    )
    listing.add_argument("--start", type=date.fromisoformat)
    listing.add_argument("--end", type=date.fromisoformat)
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--include-acknowledged", action="store_true")
    case = actions.add_parser(
        "show", help="create a revision-bound case with representative samples"
    )
    case.add_argument("--start", type=date.fromisoformat)
    case.add_argument("--end", type=date.fromisoformat)
    case.add_argument("--origin")
    case.add_argument("--destination")
    case.add_argument("--reason", choices=sorted(KNOWN_REASONS))
    case.add_argument("--reference-result", choices=sorted(RESULTS))
    case.add_argument("--provider", default="adsblol")
    case.add_argument("--record-id")
    case.add_argument("--samples", type=int, default=5)
    case.add_argument("--selection", type=Path, help="a saved cohort or review case JSON")
    if reports:
        listing.add_argument("--report", type=Path)
        case.add_argument("--report", type=Path)
    if writes:
        decision = actions.add_parser(
            "decide", help="acknowledge a cohort or review exactly one record"
        )
        decision.add_argument("--case", type=Path, required=True)
        decision.add_argument(
            "--action", choices=("acknowledge", "approve", "reject", "correct"), required=True
        )
        decision.add_argument("--note", required=True)
        decision.add_argument(
            "--replacement", type=Path, help="corrected FlightRecord JSON; correct only"
        )
        decision.add_argument("--report", type=Path)


def read_command(path, args):
    if args.review_action == "queue":
        return queue(
            path,
            start=args.start,
            end=args.end,
            limit=args.limit,
            include_acknowledged=args.include_acknowledged,
        )
    if args.selection is not None:
        value = json.loads(args.selection.read_text())
        selection = value.get("selection", value)
    else:
        selection = dict(
            source=args.provider,
            start=str(args.start) if args.start else None,
            end=str(args.end) if args.end else None,
            origin=args.origin,
            destination=args.destination,
            reason=args.reason,
            reference_result=args.reference_result,
            record_key=args.record_id,
        )
    return show(path, selection, sample_limit=args.samples)
