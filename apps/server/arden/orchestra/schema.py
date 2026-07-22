from typing import Any

from pydantic import BaseModel, create_model

# Structured result schemas for workflow agents. The worker returns a normal
# answer; the orchestra runs a formatter pass with provider-native
# response_format and validates the returned JSON against this model.

_LEAF_TYPES: dict[str, type] = {"str": str, "int": int, "float": float, "bool": bool}


def _is_model(schema: Any) -> bool:
    return isinstance(schema, type) and issubclass(schema, BaseModel)


def model_from_schema(schema: Any, name: str = "StructuredOutput") -> type[BaseModel]:
    """A pydantic model is used as-is; a plain dict shape is built into one so
    scripts can stay free of pydantic boilerplate (e.g. {"facts": ["str"]})."""
    if _is_model(schema):
        return schema
    return _model_from_dict(schema, name)


def _type_from_spec(spec: Any, name: str) -> Any:
    if isinstance(spec, str):
        if spec not in _LEAF_TYPES:
            raise ValueError(f"unknown leaf type {spec!r} in workflow schema (use str/int/float/bool)")
        return _LEAF_TYPES[spec]
    if isinstance(spec, list):
        if len(spec) != 1:
            raise ValueError("a list in a workflow schema must hold exactly one element type")
        return list[_type_from_spec(spec[0], name)]
    if isinstance(spec, dict):
        return _model_from_dict(spec, name)
    raise ValueError(f"unsupported workflow schema spec: {spec!r}")


def _model_from_dict(schema: Any, name: str) -> type[BaseModel]:
    if not isinstance(schema, dict) or not schema:
        raise ValueError("workflow schema must be a non-empty dict or a pydantic model")
    fields = {key: (_type_from_spec(spec, f"{name}_{key}"), ...) for key, spec in schema.items()}
    return create_model(name, **fields)
