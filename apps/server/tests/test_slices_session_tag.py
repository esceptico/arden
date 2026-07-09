"""Sessions and slices are joined by project_id only (unification): the
retired slice_key column has no ORM surface — the boot migration is the one
place allowed to read it (covered in test_slices_migration)."""

from ntrp.context.models import SessionState


def test_session_state_has_no_slice_key():
    assert "slice_key" not in {f for f in SessionState.__dataclass_fields__}
