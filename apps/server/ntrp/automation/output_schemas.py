"""Named output schemas an automation can request by name (data on the row,
models in code). The run then ends with one constrained completion producing
the object — delivered on RunCompleted.structured_output."""

from pydantic import BaseModel

from ntrp.areas.agent import AreaCustodianReport

OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "area_ask": AreaCustodianReport,
    "area_custodian": AreaCustodianReport,
}


def resolve_output_schema(name: str | None) -> type[BaseModel] | None:
    if name is None:
        return None
    schema = OUTPUT_SCHEMAS.get(name)
    if schema is None:
        raise ValueError(f"Unknown output schema {name!r}; valid options: {sorted(OUTPUT_SCHEMAS)}")
    return schema
