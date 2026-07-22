from ntrp.commands.models import AppDestination

COMMAND_INSTRUCTIONS = """You are the in-app command agent.
Resolve the user's command using the available canonical tools.

Rules:
- Use tools to resolve named resources before returning a destination.
- Perform requested actions through tools; their approval policy is authoritative.
- Never invent IDs or destinations.
- Return a destination only when it is validated and useful to open now.
- If multiple resources match, return needs_input with concise choices.
- If the request is not an app navigation or supported tool action, return unsupported.
- Return only the structured CommandOutcome."""


def command_context(current_destination: AppDestination | None) -> list[dict]:
    current = current_destination.model_dump_json() if current_destination else "null"
    return [
        {
            "type": "context",
            "content_type": "command_surface",
            "content": f"{COMMAND_INSTRUCTIONS}\nCurrent destination: {current}",
            "metadata": {"source": "command_palette"},
        }
    ]
