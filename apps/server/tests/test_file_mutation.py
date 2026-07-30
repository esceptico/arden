from arden.tools.core.file_mutation import RevisionConflict


def test_revision_conflict_string_hides_backend_hashes() -> None:
    expected = "a" * 64
    observed = "b" * 64

    conflict = RevisionConflict(expected=expected, observed=observed)

    assert conflict.expected == expected
    assert conflict.observed == observed
    assert str(conflict) == "file changed during update"
    assert expected not in str(conflict)
    assert observed not in str(conflict)
