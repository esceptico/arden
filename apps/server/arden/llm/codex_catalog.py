from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CODEX_DEFAULT_MAX_OUTPUT_TOKENS = 128_000


class CodexCatalogError(ValueError):
    """The Codex catalog does not satisfy the provider contract."""


class _ReasoningLevel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    effort: str = Field(min_length=1)


class _EligibleCodexModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    slug: str = Field(min_length=1)
    context_window: int = Field(gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    supported_reasoning_levels: list[_ReasoningLevel]
    supports_search_tool: bool


@dataclass(frozen=True)
class CodexModelSpec:
    slug: str
    context_window: int
    max_output_tokens: int
    reasoning_efforts: tuple[str, ...]
    native_deferred_tools: bool


def decode_codex_catalog(payload: object) -> list[CodexModelSpec]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise CodexCatalogError("Codex catalog must contain a models array")

    models: list[CodexModelSpec] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload["models"]):
        if not isinstance(raw, dict):
            raise CodexCatalogError(f"Codex catalog model {index} must be an object")
        visibility = raw.get("visibility")
        supported_in_api = raw.get("supported_in_api")
        if not isinstance(visibility, str) or not isinstance(supported_in_api, bool):
            raise CodexCatalogError(f"Codex catalog model {index} has invalid visibility metadata")
        if visibility != "list" or not supported_in_api:
            continue
        try:
            entry = _EligibleCodexModel.model_validate(raw)
        except ValidationError as error:
            raise CodexCatalogError(f"Codex catalog model {index} is invalid: {error}") from error
        if entry.slug in seen:
            raise CodexCatalogError(f"Codex catalog contains duplicate slug {entry.slug!r}")
        seen.add(entry.slug)
        models.append(
            CodexModelSpec(
                slug=entry.slug,
                context_window=entry.context_window,
                max_output_tokens=entry.max_output_tokens or CODEX_DEFAULT_MAX_OUTPUT_TOKENS,
                reasoning_efforts=tuple(level.effort for level in entry.supported_reasoning_levels),
                native_deferred_tools=entry.supports_search_tool,
            )
        )
    return models
