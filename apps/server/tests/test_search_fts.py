from arden.search.fts import build_fts_or_query


def test_build_fts_or_query_caps_terms_and_dedupes():
    query = " ".join(["alpha", "beta", "alpha", *[f"term{i}" for i in range(200)]])

    fts = build_fts_or_query(query, max_terms=4, max_chars=10_000)

    assert fts == '"alpha" OR "beta" OR "term0" OR "term1"'


def test_build_fts_or_query_caps_input_chars():
    fts = build_fts_or_query("first " + ("x" * 1000) + " last", max_terms=10, max_chars=20)

    assert "first" in fts
    assert "last" not in fts
