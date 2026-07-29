"""Canonical JSONL records and strict fact-event field validation."""

import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from arden.memory.facts.models import FactEvent, FactLedgerCorruptionError, FactValidationError

VERSION = 1
MONTH = re.compile(r"^\d{4}-\d{2}\.jsonl$")
OPS = frozenset({"create", "review", "amend", "supersede", "expire", "retract"})
LIFECYCLES = frozenset({"durable", "temporary"})
CERTAINTIES = frozenset({"confirmed", "uncertain"})
EVIDENCE = frozenset({"direct", "inferred"})
SCOPE_KINDS = frozenset({"user", "area", "global", "project", "integration"})
REVIEW_BASES = frozenset({"explicit", "inferred", "fallback"})
TIME_PRECISIONS = frozenset({"millisecond", "second", "minute", "day", "unknown"})
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")
SOURCE_REQUIRED = frozenset({"kind", "ref"})
SOURCE_OPTIONAL = frozenset(
    {
        "captured_at",
        "scope_kind",
        "scope_key",
        "occurred_at",
        "time_precision",
        "role",
        "excerpt_hash",
        "extra",
    }
)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def chain_version(previous: str, record: Mapping[str, Any]) -> str:
    return hashlib.sha256((previous + "\0" + canonical(record)).encode()).hexdigest()


def text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise FactValidationError(f"{field} must be a non-empty string")
    return value


def reference(value: object) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise FactValidationError("source.ref must be a string without NUL")
    return value


def uuid_value(value: object, field: str) -> str:
    value = text(value, field)
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise FactValidationError(f"{field} must be a UUID") from exc
    if str(parsed) != value:
        raise FactValidationError(f"{field} must be a canonical UUID")
    return value


def parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC.fullmatch(value) is None:
        raise FactValidationError(f"{field} must be an RFC3339 UTC string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FactValidationError(f"invalid {field}") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise FactValidationError(f"{field} must be UTC")
    return result.astimezone(UTC)


def time_value(value: datetime) -> str:
    if value.tzinfo is None:
        raise FactValidationError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def optional_time(value: object, field: str) -> str | None:
    return None if value is None else time_value(parse_time(value, field))


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def strings(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise FactValidationError(f"{field} must be a list")
    result = tuple(text(item, field) for item in value)
    if required and not result:
        raise FactValidationError(f"{field} must not be empty")
    if len(set(result)) != len(result):
        raise FactValidationError(f"{field} must not contain duplicates")
    return result


def scope(value: object) -> dict[str, str | None]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "key"}:
        raise FactValidationError("scope must contain exactly kind and key")
    kind = text(value["kind"], "scope.kind")
    if kind not in SCOPE_KINDS:
        raise FactValidationError("invalid scope kind")
    return {"kind": kind, "key": scope_key(kind, value["key"], "scope.key")}


def scope_key(kind: str, value: object, field: str) -> str | None:
    if kind in {"global", "user"}:
        if value is not None:
            raise FactValidationError(f"{field} must be null for {kind} scope")
        return None
    return text(value, field)


def source(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FactValidationError("each source must be an object")
    if not set(value) >= SOURCE_REQUIRED or set(value) - SOURCE_REQUIRED - SOURCE_OPTIONAL:
        raise FactValidationError("invalid SourceRef fields")
    result: dict[str, Any] = {"kind": text(value["kind"], "source.kind"), "ref": reference(value["ref"])}
    for field in SOURCE_OPTIONAL - {"extra"}:
        if field not in value:
            continue
        item = value[field]
        result[field] = None if item is None else text(item, f"source.{field}")
    if "extra" in value:
        if not isinstance(value["extra"], Mapping):
            raise FactValidationError("source.extra must be an object")
        result["extra"] = json_value(value["extra"], "source.extra")
    if result["kind"] == "fact":
        result["ref"] = text(result["ref"], "source.ref")
        extra = result.get("extra")
        version = extra.get("version") if isinstance(extra, Mapping) else None
        if not isinstance(version, str) or re.fullmatch(r"[0-9a-f]{64}", version) is None:
            raise FactValidationError("fact source requires an exact version")
    precision = result.get("time_precision")
    if "time_precision" in result and precision not in TIME_PRECISIONS:
        raise FactValidationError("invalid source.time_precision")
    scope_kind = result.get("scope_kind")
    scope_value = result.get("scope_key")
    if scope_kind is None:
        if scope_value is not None:
            raise FactValidationError("source scope_kind and scope_key must appear together")
    else:
        if scope_kind not in SCOPE_KINDS:
            raise FactValidationError("invalid source.scope_kind")
        result["scope_key"] = scope_key(scope_kind, scope_value, "source.scope_key")
    if result.get("captured_at") is not None:
        parse_time(result["captured_at"], "source.captured_at")
    occurred_at = result.get("occurred_at")
    if error := source_precision_error(occurred_at, precision):
        raise FactValidationError(error)
    if occurred_at is not None:
        if precision == "day":
            try:
                date.fromisoformat(occurred_at)
            except ValueError as exc:
                raise FactValidationError("invalid source.occurred_at") from exc
        else:
            parse_time(occurred_at, "source.occurred_at")
    return result


def sources(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise FactValidationError("sources must be a non-empty list")
    return tuple(source(item) for item in value)


def decode_event(record: object) -> FactEvent:
    expected = {
        "schema_version",
        "event_id",
        "plan_id",
        "fact_id",
        "op",
        "occurred_at",
        "actor",
        "origin",
        "plan_reason",
        "payload",
    }
    if not isinstance(record, Mapping) or set(record) != expected:
        raise FactLedgerCorruptionError("unknown event fields")
    try:
        if type(record["schema_version"]) is not int or record["schema_version"] != VERSION:
            raise FactValidationError("unknown event schema version")
        event_id = uuid_value(record["event_id"], "event_id")
        plan_id = uuid_value(record["plan_id"], "plan_id")
        fact_id = text(record["fact_id"], "fact_id")
        op = record["op"]
        if op not in OPS or not isinstance(record["payload"], Mapping):
            raise FactValidationError("invalid event operation")
        occurred_at = parse_time(record["occurred_at"], "occurred_at")
        actor = text(record["actor"], "actor")
        origin = text(record["origin"], "origin")
        plan_reason = text(record["plan_reason"], "plan_reason")
        validate_payload(str(op), record["payload"])
    except FactValidationError as exc:
        raise FactLedgerCorruptionError(str(exc)) from exc
    return FactEvent(event_id, plan_id, fact_id, str(op), occurred_at, actor, origin, plan_reason, dict(record))


def make_event(
    plan_id: str,
    fact_id: str,
    op: str,
    point: datetime,
    payload: Mapping[str, Any],
    actor: str,
    origin: str,
    plan_reason: str,
) -> FactEvent:
    record = {
        "schema_version": VERSION,
        "event_id": str(uuid.uuid4()),
        "plan_id": plan_id,
        "fact_id": fact_id,
        "op": op,
        "occurred_at": time_value(point),
        "actor": actor,
        "origin": origin,
        "plan_reason": plan_reason,
        "payload": dict(payload),
    }
    return FactEvent(record["event_id"], plan_id, fact_id, op, point, actor, origin, plan_reason, record)


def parse_event_file(path: str, content: bytes) -> tuple[FactEvent, ...]:
    text_value = utf8_jsonl(content, path)
    events = []
    for line in text_value.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FactLedgerCorruptionError(f"malformed JSON in {path}") from exc
        if not isinstance(record, dict) or canonical(record) != line:
            raise FactLedgerCorruptionError(f"non-canonical JSON in {path}")
        event = decode_event(record)
        if event.occurred_at.strftime("%Y-%m.jsonl") != path:
            raise FactLedgerCorruptionError("event is in the wrong monthly file")
        events.append(event)
    return tuple(events)


def validate_payload(op: str, payload: Mapping[str, Any]) -> None:
    if op == "create":
        expected = {
            "text",
            "normalized_text",
            "kind",
            "labels",
            "subjects",
            "scope",
            "lifecycle",
            "status",
            "certainty",
            "evidence_class",
            "sources",
            "review_at",
            "review_basis",
            "expires_at",
        }
        if set(payload) != expected:
            raise FactValidationError("invalid create payload")
        value = text(payload["text"], "text")
        if payload["normalized_text"] != normalized(value) or payload["status"] != "active":
            raise FactValidationError("invalid initial fact state")
        text(payload["kind"], "kind")
        strings(payload["labels"], "labels")
        strings(payload["subjects"], "subjects", required=True)
        scope(payload["scope"])
        sources(payload["sources"])
        if payload["lifecycle"] not in LIFECYCLES:
            raise FactValidationError("invalid lifecycle")
        if payload["certainty"] not in CERTAINTIES:
            raise FactValidationError("invalid certainty")
        if payload["evidence_class"] not in EVIDENCE:
            raise FactValidationError("invalid evidence_class")
        validate_review_pair(payload["review_at"], payload["review_basis"])
        if payload["expires_at"] is not None:
            parse_time(payload["expires_at"], "expires_at")
        if payload["lifecycle"] == "durable" and payload["expires_at"] is not None:
            raise FactValidationError("durable facts must not have expires_at")
        if payload["lifecycle"] == "temporary" and payload["expires_at"] is None and payload["review_at"] is None:
            raise FactValidationError("temporary fact needs fallback review")
        if payload["expires_at"] is not None and payload["review_basis"] == "fallback":
            raise FactValidationError("explicit expiry must not use fallback review")
        return
    allowed = {
        "review": {"reason", "review_at", "review_basis", "sources", "certainty", "review_window"},
        "amend": {
            "kind",
            "labels",
            "subjects",
            "scope",
            "lifecycle",
            "evidence_class",
            "sources",
            "review_at",
            "review_basis",
            "expires_at",
        },
        "supersede": {"successor_id", "reason", "sources", "review_window", "maintenance_merge"},
        "expire": {"reason", "sources", "review_window"},
        "retract": {"reason", "sources"},
    }[op]
    required = {
        "review": {"reason", "review_at", "review_basis"},
        "amend": set(),
        "supersede": {"successor_id", "reason"},
        "expire": {"reason"},
        "retract": {"reason"},
    }[op]
    if not required <= set(payload) or set(payload) - allowed:
        raise FactValidationError("invalid event payload")
    if op == "amend" and not payload:
        raise FactValidationError("empty amendment")
    if "reason" in payload:
        text(payload["reason"], "reason")
    if "successor_id" in payload:
        text(payload["successor_id"], "successor_id")
    if "kind" in payload:
        text(payload["kind"], "kind")
    if "labels" in payload:
        strings(payload["labels"], "labels")
    if "subjects" in payload:
        strings(payload["subjects"], "subjects", required=True)
    if "scope" in payload:
        scope(payload["scope"])
    if op == "amend":
        validate_amend_payload(payload)
    if "evidence_class" in payload and payload["evidence_class"] not in EVIDENCE:
        raise FactValidationError("invalid evidence_class")
    if "sources" in payload:
        sources(payload["sources"])
    if "certainty" in payload and payload["certainty"] not in CERTAINTIES:
        raise FactValidationError("invalid certainty")
    if "review_window" in payload:
        fact_version(payload["review_window"], "review_window")
    if "maintenance_merge" in payload and payload["maintenance_merge"] is not True:
        raise FactValidationError("maintenance_merge must be true")
    if "review_at" in payload or "review_basis" in payload:
        if not {"review_at", "review_basis"} <= set(payload):
            raise FactValidationError("review fields must appear together")
        validate_review_pair(payload["review_at"], payload["review_basis"])


def validate_amend_payload(payload: Mapping[str, Any]) -> None:
    if "lifecycle" in payload:
        if payload["lifecycle"] not in LIFECYCLES:
            raise FactValidationError("invalid lifecycle")
        if not {"review_at", "review_basis", "expires_at"} <= set(payload):
            raise FactValidationError("lifecycle amendment requires complete timing fields")
        validate_review_pair(payload["review_at"], payload["review_basis"])
        if payload["expires_at"] is not None:
            parse_time(payload["expires_at"], "expires_at")
        if payload["lifecycle"] == "durable" and (
            payload["review_at"] is not None or payload["expires_at"] is not None
        ):
            raise FactValidationError("durable facts must not have review or expiry dates")
        if payload["lifecycle"] == "temporary" and payload["review_at"] is None and payload["expires_at"] is None:
            raise FactValidationError("temporary facts require review or expiry")
    elif {"review_at", "review_basis", "expires_at"} & set(payload):
        raise FactValidationError("fact timing fields require lifecycle")


def validate_review_pair(review_at: object, review_basis: object) -> None:
    if review_at is None:
        if review_basis is not None:
            raise FactValidationError("review_basis requires review_at")
        return
    parse_time(review_at, "review_at")
    if review_basis not in REVIEW_BASES:
        raise FactValidationError("invalid review_basis")


def fact_version(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise FactValidationError(f"{field} must be a SHA-256 digest")
    return value


def window_key_value(value: object) -> str:
    if not isinstance(value, str):
        raise FactLedgerCorruptionError("invalid duplicate-window key")
    try:
        parts = json.loads(value)
        if not isinstance(parts, list) or len(parts) != 3 or canonical(parts) != value:
            raise FactValidationError("invalid duplicate-window key")
        scope({"kind": parts[0], "key": parts[1]})
        text(parts[2], "duplicate-window component")
    except FactValidationError as exc:
        raise FactLedgerCorruptionError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise FactLedgerCorruptionError("invalid duplicate-window key") from exc
    return value


def window_parts(value: str) -> tuple[str, str | None, str]:
    window_key_value(value)
    kind, key, normalized_text = json.loads(value)
    return kind, key, normalized_text


def utf8_jsonl(content: bytes, path: str) -> str:
    try:
        result = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FactLedgerCorruptionError(f"{path} is not UTF-8") from exc
    if not result or not result.endswith("\n"):
        raise FactLedgerCorruptionError(f"{path} is not canonical JSONL")
    return result


def json_value(value: object, field: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FactValidationError(f"{field} must be JSON-safe")
        return value
    if isinstance(value, (list, tuple)):
        return [json_value(item, field) for item in value]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise FactValidationError(f"{field} keys must be strings")
            result[key] = json_value(item, field)
        return result
    raise FactValidationError(f"{field} must be JSON-safe")


def source_precision_error(value: object, precision: object) -> str | None:
    if precision in (None, "unknown"):
        return None if value is None else "source.occurred_at requires known time_precision"
    if value is None:
        return "known source.time_precision requires occurred_at"
    if precision == "day":
        return (
            None
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            else "day source.time_precision requires a date"
        )
    if not isinstance(value, str):
        return f"{precision} source.time_precision requires an RFC3339 timestamp"
    timestamp = re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:(?P<seconds>\d{2})(?P<fraction>\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
    )
    if timestamp is None:
        return f"{precision} source.time_precision requires an RFC3339 timestamp"
    fraction = timestamp["fraction"]
    if precision == "minute" and (timestamp["seconds"] != "00" or fraction is not None):
        return "minute source.time_precision requires zero seconds and no fraction"
    if precision == "second" and fraction is not None:
        return "second source.time_precision forbids fractional seconds"
    if precision == "millisecond" and (fraction is None or len(fraction) != 4):
        return "millisecond source.time_precision requires exactly three fractional digits"
    return None
