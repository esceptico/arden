from typing import Any

from arden.tool_call_metadata import DISPLAY_TITLE_ARG, RESERVED_TOOL_ARGUMENTS

DISPLAY_TITLE_PROPERTY = {
    "type": "string",
    "description": (
        "Short UI action title — a 3-6 word present-continuous phrase naming what "
        'this call does for the user (e.g. "Searching email for the invoice", '
        '"Reading the design doc", "Checking your calendar"). A display label only, '
        "not part of the tool's work; optional."
    ),
}


def _local_ref(root: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local JSON Schema references can be normalized: {ref}")
    value: Any = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"Unresolvable local JSON Schema reference: {ref}")
        value = value[part]
    return value


def normalize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local refs without discarding constraints or combinators.

    `discriminator` goes with `$defs`: its `mapping` values are `#/$defs/...`
    pointers, which dangle once the definitions are inlined away. Each member of
    a discriminated union already pins its own tag value, so nothing is lost.
    """
    root = schema
    dropped = {"$defs", "definitions", "discriminator"}

    def resolve(node: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(node, list):
            return [resolve(item, stack) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref in stack:
                raise ValueError(f"Recursive local JSON Schema reference is unsupported: {ref}")
            target = resolve(_local_ref(root, ref), (*stack, ref))
            siblings = resolve({key: value for key, value in node.items() if key != "$ref"}, stack)
            if siblings:
                return {"allOf": [target, siblings]}
            return target
        return {key: resolve(value, stack) for key, value in node.items() if key not in dropped}

    normalized = resolve(root)
    if not isinstance(normalized, dict):
        raise ValueError("Tool input schema must normalize to an object")
    return normalized


def tool_parameters(input_schema: dict[str, Any], *, tool_name: str) -> dict[str, Any]:
    parameters = normalize_json_schema(input_schema)
    properties = dict(parameters.get("properties") or {})
    collisions = RESERVED_TOOL_ARGUMENTS.intersection(properties)
    if collisions:
        names = ", ".join(sorted(collisions))
        raise ValueError(f"Tool {tool_name!r} schema uses reserved tool argument(s): {names}")
    parameters["type"] = parameters.get("type", "object")
    parameters["properties"] = {DISPLAY_TITLE_ARG: DISPLAY_TITLE_PROPERTY, **properties}
    parameters.setdefault("required", [])
    return parameters
