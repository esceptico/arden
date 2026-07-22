from arden.services.token_directive import parse_token_budget


def test_parses_k_and_m_units():
    assert parse_token_budget("+500k do a deep review") == 500_000
    assert parse_token_budget("review this +1m") == 1_000_000
    assert parse_token_budget("+1.5m") == 1_500_000
    assert parse_token_budget("+50K") == 50_000  # case-insensitive


def test_requires_unit_and_deliberate_plus():
    assert parse_token_budget("hello") is None
    assert parse_token_budget("") is None
    assert parse_token_budget("+200000") is None  # no unit → not a budget
    assert parse_token_budget("2+5k") is None  # not at start / after whitespace
    assert parse_token_budget("a+5k") is None
    assert parse_token_budget("the cost is $5 + tax") is None


def test_first_directive_wins():
    assert parse_token_budget("+500k then +1m") == 500_000
