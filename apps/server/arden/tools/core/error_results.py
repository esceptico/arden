from collections.abc import Iterable
from difflib import get_close_matches

from arden.tools.core.base import ToolResult


def invalid_ref_result(
    resource: str,
    ref: str,
    *,
    candidates: Iterable[str] = (),
    call_first: str | None = None,
) -> ToolResult:
    nearby = get_close_matches(ref, list(candidates), n=3, cutoff=0.5)
    recovery = f"Call {call_first} first to obtain a current {resource} reference." if call_first else None
    if nearby:
        recovery = f"Try one of these current references: {', '.join(nearby)}."
    return ToolResult.failure(
        code="invalid_ref",
        message=f"Unknown {resource} reference: {ref}",
        preview="Invalid reference",
        recovery_action=recovery,
    )


def not_found_result(resource: str, ref: str, *, call_first: str) -> ToolResult:
    return ToolResult.failure(
        code="not_found",
        message=f"{resource.capitalize()} not found: {ref}",
        preview="Not found",
        recovery_action=f"Call {call_first} first to obtain a current {resource} reference.",
    )
