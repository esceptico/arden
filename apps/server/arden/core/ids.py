from uuid import uuid4

from coolname import generate_slug


def generate_run_id() -> str:
    """Human-readable run ID with enough entropy for durable uniqueness."""
    return f"{generate_slug(2)}-{uuid4().hex[:12]}"
