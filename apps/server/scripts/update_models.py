import json
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

MODELS_DEV_URL = "https://models.dev/api.json"
OUT_PATH = Path(__file__).resolve().parents[1] / "arden" / "llm" / "generated_models.json"
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


def main() -> None:
    models = sorted(iter_filtered_models(fetch_models_dev()), key=lambda m: (m["provider"], m["id"]))
    OUT_PATH.write_text(json.dumps(models, indent=2) + "\n")

    counts: dict[str, int] = {}
    for model in models:
        counts[model["provider"]] = counts.get(model["provider"], 0) + 1
    print(f"Wrote {OUT_PATH}")
    print(", ".join(f"{provider}: {count}" for provider, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
