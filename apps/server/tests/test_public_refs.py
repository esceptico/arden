import pytest

from arden.core.public_refs import is_public_ref, public_ref


def test_public_ref_is_deterministic_and_identity_sensitive() -> None:
    first = public_ref("Plan launch", "internal-1", empty_slug="item")
    assert first == public_ref("Plan launch", "internal-1", empty_slug="item")
    assert first.startswith("plan-launch~")
    assert first != public_ref("Plan launch", "internal-2", empty_slug="item")


def test_public_ref_validation_is_exact() -> None:
    value = public_ref("fact-plan", "plan-1", empty_slug="fact-plan")
    assert is_public_ref(value)
    assert is_public_ref(value, slug="fact-plan")
    assert not is_public_ref(value, slug="other")
    assert not is_public_ref("plan-1")
    assert not is_public_ref(f"{value}-extra")


def test_public_ref_requires_explicit_non_ascii_slug() -> None:
    value = public_ref("计划", "internal-1", empty_slug="fact")
    assert value.startswith("fact~")
    with pytest.raises(ValueError, match="non-empty"):
        public_ref("", "internal-1", empty_slug="fact")
