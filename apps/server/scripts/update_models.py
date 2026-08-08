import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from arden.llm.codex_catalog import decode_codex_catalog
from arden.llm.openai_codex_auth import CODEX_CLIENT_VERSION

MODELS_DEV_URL = "https://models.dev/api.json"
OUT_PATH = Path(__file__).resolve().parents[1] / "arden" / "llm" / "generated_models.json"
CODEX_CACHE_PATH = Path.home() / ".codex" / "models_cache.json"
CODEX_CACHE_MAX_AGE = timedelta(days=1)
PROVIDERS = ("openai", "anthropic", "google", "openrouter")
REASONING_EFFORT_OVERRIDES = {
    "claude-opus-4-7": ["low", "medium", "high", "xhigh", "max"],
    "claude-opus-4-6": ["low", "medium", "high", "max"],
    "claude-sonnet-4-6": ["low", "medium", "high", "max"],
    "claude-haiku-4-5-20251001": ["high", "max"],
    "gpt-5.5": ["none", "low", "medium", "high", "xhigh"],
    "gpt-5.4": ["none", "low", "medium", "high", "xhigh"],
    "gpt-5.4-mini": ["none", "low", "medium", "high", "xhigh"],
    "gpt-5.4-nano": ["none", "low", "medium", "high"],
    "gpt-5.2": ["minimal", "low", "medium", "high", "xhigh"],
    "gemini-3-flash-preview": ["low", "high"],
}


def iter_filtered_models(data: dict, *, today: date | None = None) -> Iterable[dict]:
    cutoff = (today or date.today()) - timedelta(days=365)
    for provider in PROVIDERS:
        models = data.get(provider, {}).get("models") or {}
        for model_id, raw in models.items():
            entry = normalize_model(provider, model_id, raw)
            if entry is not None and passes_filter(entry, cutoff=cutoff):
                yield entry


def normalize_model(provider: str, model_id: str, raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    limit = raw.get("limit") if isinstance(raw.get("limit"), dict) else {}
    cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}
    modalities = raw.get("modalities") if isinstance(raw.get("modalities"), dict) else {}
    return {
        "id": model_id,
        "provider": provider,
        "name": raw.get("name") or model_id,
        "tool_call": raw.get("tool_call") is True,
        "structured_output": raw.get("structured_output") is True,
        "reasoning": raw.get("reasoning") is True,
        "input_modalities": list(modalities.get("input") or ()),
        "output_modalities": list(modalities.get("output") or ()),
        "context_window": int(limit.get("context") or 0),
        "max_output_tokens": int(limit.get("output") or 0),
        "price_in": float(cost.get("input") or 0),
        "price_out": float(cost.get("output") or 0),
        "price_cache_read": float(cost.get("cache_read") or 0),
        "price_cache_write": float(cost.get("cache_write") or 0),
        "reasoning_efforts": reasoning_efforts(provider, raw),
        "release_date": raw.get("release_date"),
        "last_updated": raw.get("last_updated"),
    }


def passes_filter(model: dict, *, cutoff: date) -> bool:
    if not model["tool_call"]:
        return False
    if "text" not in model["input_modalities"] or "text" not in model["output_modalities"]:
        return False
    if model["context_window"] < 64_000:
        return False
    if not is_recent(model["last_updated"], cutoff=cutoff):
        return False
    if model["provider"] != "openrouter":
        return True
    return model["structured_output"] and model["max_output_tokens"] >= 16_000 and not str(model["id"]).startswith("~")


def is_recent(value: object, *, cutoff: date) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value) >= cutoff
    except ValueError:
        return False


def reasoning_efforts(provider: str, raw: dict) -> list[str]:
    if raw.get("id") in REASONING_EFFORT_OVERRIDES:
        return REASONING_EFFORT_OVERRIDES[raw["id"]]
    if raw.get("reasoning") is not True:
        return []
    if provider == "anthropic":
        return ["low", "medium", "high", "max"]
    if provider in {"openai", "google"}:
        return ["low", "medium", "high"]
    return []


def fetch_models_dev() -> dict:
    request = Request(MODELS_DEV_URL, headers={"User-Agent": "arden-model-registry/1.0"})
    with urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("models.dev response must be an object")
    return data


def runtime_model_entry(model: dict) -> dict:
    provider = model["provider"]
    model_id = model["id"]
    return {
        "id": model_id,
        "provider": provider,
        "context_window": model["context_window"],
        "max_output_tokens": model["max_output_tokens"],
        "price_in": model["price_in"],
        "price_out": model["price_out"],
        "price_cache_read": model["price_cache_read"],
        "price_cache_write": model["price_cache_write"],
        "reasoning_efforts": model["reasoning_efforts"],
        "native_deferred_tools": (provider == "anthropic" and "haiku" not in model_id)
        or (provider == "openai" and model_id.startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6"))),
    }


def load_codex_cache(path: Path = CODEX_CACHE_PATH, *, now: datetime | None = None) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"Codex model cache at {path} is unavailable or invalid") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"Codex model cache at {path} must be a JSON object")
    if payload.get("client_version") != CODEX_CLIENT_VERSION:
        raise RuntimeError(f"Codex model cache at {path} does not match client {CODEX_CLIENT_VERSION}")
    try:
        fetched_at = datetime.fromisoformat(payload["fetched_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError) as error:
        raise RuntimeError(f"Codex model cache at {path} has an invalid fetched_at timestamp") from error
    if fetched_at.tzinfo is None:
        raise RuntimeError(f"Codex model cache at {path} has an invalid fetched_at timestamp")
    age = (now or datetime.now(UTC)) - fetched_at
    if age < timedelta(0) or age > CODEX_CACHE_MAX_AGE:
        raise RuntimeError(f"Codex model cache at {path} is stale")
    return payload


def codex_model_entries(payload: object) -> list[dict]:
    return [
        {
            "id": f"openai-codex/{spec.slug}",
            "provider": "openai-codex",
            "context_window": spec.context_window,
            "max_output_tokens": spec.max_output_tokens,
            "price_in": 0.0,
            "price_out": 0.0,
            "price_cache_read": 0.0,
            "price_cache_write": 0.0,
            "reasoning_efforts": list(spec.reasoning_efforts),
            "native_deferred_tools": spec.native_deferred_tools,
        }
        for spec in decode_codex_catalog(payload)
    ]


def main() -> None:
    models = [runtime_model_entry(model) for model in iter_filtered_models(fetch_models_dev())]
    models.extend(codex_model_entries(load_codex_cache()))
    models.sort(key=lambda model: (model["provider"], model["id"]))
    OUT_PATH.write_text(json.dumps(models, indent=2) + "\n")

    counts: dict[str, int] = {}
    for model in models:
        counts[model["provider"]] = counts.get(model["provider"], 0) + 1
    print(f"Wrote {OUT_PATH}")
    print(", ".join(f"{provider}: {count}" for provider, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
