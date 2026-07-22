"""Sessions and areas are joined by area_id only (unification): the
retired area_key column has no ORM surface — the boot migration is the one
place allowed to read it (covered in test_areas_migration)."""

from arden.context.models import SessionState


def test_session_state_has_no_area_key():
    assert "area_key" not in set(SessionState.__dataclass_fields__)
