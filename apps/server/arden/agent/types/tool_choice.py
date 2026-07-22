from dataclasses import dataclass
from enum import StrEnum


class ToolChoiceMode(StrEnum):
    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"


@dataclass(frozen=True)
class SpecificTool:
    name: str


ToolChoice = ToolChoiceMode | SpecificTool
