import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class ToolOutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    UNCERTAIN = "uncertain"


class ToolOutcomePayloadError(ValueError):
    """A persisted tool outcome violated the typed wire contract."""


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    retryable: bool = False
    recovery_action: str | None = None
    diagnostic_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.code, "tool error code")
        if not isinstance(self.retryable, bool):
            raise TypeError("tool error retryable must be a boolean")
        _require_optional_text(self.recovery_action, "tool error recovery_action")
        _require_optional_text(self.diagnostic_ref, "tool error diagnostic_ref")

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"code": self.code, "retryable": self.retryable}
        if self.recovery_action:
            data["recovery_action"] = self.recovery_action
        if self.diagnostic_ref:
            data["diagnostic_ref"] = self.diagnostic_ref
        return data


@dataclass(frozen=True, slots=True)
class ToolEffect:
    operation: str
    target: str
    before_ref: str | None = None
    after_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.operation, "tool effect operation")
        _require_text(self.target, "tool effect target")
        _require_optional_text(self.before_ref, "tool effect before_ref")
        _require_optional_text(self.after_ref, "tool effect after_ref")

    def to_dict(self) -> dict[str, str]:
        data = {"operation": self.operation, "target": self.target}
        if self.before_ref:
            data["before_ref"] = self.before_ref
        if self.after_ref:
            data["after_ref"] = self.after_ref
        return data


@dataclass(frozen=True, slots=True)
class ToolVerification:
    postcondition: str
    observed: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.postcondition, "tool verification postcondition")
        _require_text(self.observed, "tool verification observed")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(self.confidence, int | float):
                raise TypeError("tool verification confidence must be a number")
            if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
                raise ValueError("tool verification confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, str | float]:
        data: dict[str, str | float] = {
            "postcondition": self.postcondition,
            "observed": self.observed,
        }
        if self.confidence is not None:
            data["confidence"] = self.confidence
        return data


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    status: ToolOutcomeStatus
    error: ToolError | None = None
    effect: ToolEffect | None = None
    verification: ToolVerification | None = None
    receipt: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ToolOutcomeStatus):
            raise TypeError("tool outcome status must be a ToolOutcomeStatus")
        if self.error is not None and not isinstance(self.error, ToolError):
            raise TypeError("tool outcome error must be a ToolError")
        if self.effect is not None and not isinstance(self.effect, ToolEffect):
            raise TypeError("tool outcome effect must be a ToolEffect")
        if self.verification is not None and not isinstance(self.verification, ToolVerification):
            raise TypeError("tool outcome verification must be a ToolVerification")
        _require_optional_text(self.receipt, "tool outcome receipt")
        if self.status == ToolOutcomeStatus.SUCCEEDED and self.error is not None:
            raise ValueError("successful tool outcome must not contain an error")
        if self.status != ToolOutcomeStatus.SUCCEEDED and self.error is None:
            raise ValueError("non-successful tool outcome must contain an error")

    @classmethod
    def decode(cls, value: object) -> "ToolOutcome":
        try:
            fields = _wire_object(
                value,
                label="tool outcome",
                required={"status"},
                optional={"error", "effect", "verification", "receipt"},
            )
            status_value = _wire_text(fields["status"], "tool outcome status")
            try:
                status = ToolOutcomeStatus(status_value)
            except ValueError as exc:
                raise ToolOutcomePayloadError(f"unsupported tool outcome status: {status_value}") from exc
            return cls(
                status=status,
                error=_decode_tool_error(fields["error"]) if "error" in fields else None,
                effect=_decode_tool_effect(fields["effect"]) if "effect" in fields else None,
                verification=(_decode_tool_verification(fields["verification"]) if "verification" in fields else None),
                receipt=_wire_text(fields["receipt"], "tool outcome receipt") if "receipt" in fields else None,
            )
        except ToolOutcomePayloadError:
            raise
        except (TypeError, ValueError) as exc:
            raise ToolOutcomePayloadError(str(exc)) from exc

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"status": self.status.value}
        if self.error:
            data["error"] = self.error.to_dict()
        if self.effect:
            data["effect"] = self.effect.to_dict()
        if self.verification:
            data["verification"] = self.verification.to_dict()
        if self.receipt:
            data["receipt"] = self.receipt
        return data


def tool_result_content_for_model(content: str, outcome: ToolOutcome | None) -> str:
    """Attach machine-readable recovery metadata without changing the UI result."""
    if outcome is None or outcome.status == ToolOutcomeStatus.SUCCEEDED:
        return content
    payload: dict[str, object] = {"status": outcome.status.value}
    if outcome.error is not None:
        payload["error"] = outcome.error.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{content}\n\n<tool_outcome>{encoded}</tool_outcome>"


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value


def _require_optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, label)


def _wire_text(value: object, label: str) -> str:
    try:
        return _require_text(value, label)
    except (TypeError, ValueError) as exc:
        raise ToolOutcomePayloadError(str(exc)) from exc


def _wire_object(
    value: object,
    *,
    label: str,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ToolOutcomePayloadError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ToolOutcomePayloadError(f"{label} field names must be strings")
    keys = set(value)
    missing = required - keys
    unknown = keys - required - (optional if optional is not None else set())
    if missing:
        raise ToolOutcomePayloadError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ToolOutcomePayloadError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    return value


def _decode_tool_error(value: object) -> ToolError:
    fields = _wire_object(
        value,
        label="tool error",
        required={"code", "retryable"},
        optional={"recovery_action", "diagnostic_ref"},
    )
    retryable = fields["retryable"]
    if not isinstance(retryable, bool):
        raise ToolOutcomePayloadError("tool error retryable must be a boolean")
    return ToolError(
        code=_wire_text(fields["code"], "tool error code"),
        retryable=retryable,
        recovery_action=(
            _wire_text(fields["recovery_action"], "tool error recovery_action") if "recovery_action" in fields else None
        ),
        diagnostic_ref=(
            _wire_text(fields["diagnostic_ref"], "tool error diagnostic_ref") if "diagnostic_ref" in fields else None
        ),
    )


def _decode_tool_effect(value: object) -> ToolEffect:
    fields = _wire_object(
        value,
        label="tool effect",
        required={"operation", "target"},
        optional={"before_ref", "after_ref"},
    )
    return ToolEffect(
        operation=_wire_text(fields["operation"], "tool effect operation"),
        target=_wire_text(fields["target"], "tool effect target"),
        before_ref=_wire_text(fields["before_ref"], "tool effect before_ref") if "before_ref" in fields else None,
        after_ref=_wire_text(fields["after_ref"], "tool effect after_ref") if "after_ref" in fields else None,
    )


def _decode_tool_verification(value: object) -> ToolVerification:
    fields = _wire_object(
        value,
        label="tool verification",
        required={"postcondition", "observed"},
        optional={"confidence"},
    )
    confidence = fields.get("confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, int | float)):
        raise ToolOutcomePayloadError("tool verification confidence must be a number")
    return ToolVerification(
        postcondition=_wire_text(fields["postcondition"], "tool verification postcondition"),
        observed=_wire_text(fields["observed"], "tool verification observed"),
        confidence=confidence,
    )
