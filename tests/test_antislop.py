from stockforge.forge.antislop import AntiSlop


def test_clean_concept_passes():
    a = AntiSlop()
    v = a.check(
        name="Silicon NVDA",
        symbol="SILNV",
        thesis="Silicon NVDA rides the NVDA compute-demand narrative and routes trading fees back into compute. Not affiliated with NVDA.",
    )
    assert v.ok
    assert v.score >= 0.6


def test_slop_name_penalized():
    a = AntiSlop()
    v = a.check(
        name="Baby Moon Inu",
        symbol="BMI",
        thesis="short thesis here that is long enough to pass the length gate easily now",
    )
    assert any("slop" in r for r in v.reasons)


def test_duplicate_rejected():
    a = AntiSlop()
    v = a.check(
        name="Silicon NVDA",
        symbol="SILNV",
        thesis="Silicon NVDA rides the NVDA compute narrative and routes fees into compute always.",
        recent_slugs=["silnv:silicon nvda"],
    )
    assert not v.ok
    assert any("duplicate" in r for r in v.reasons)


def test_bad_symbol_penalized():
    a = AntiSlop()
    v = a.check(name="Cool Token", symbol="lowercase!", thesis="a" * 60 + " words here now")
    assert any("symbol" in r for r in v.reasons)


def test_thin_thesis_penalized():
    a = AntiSlop()
    v = a.check(name="Cool Token", symbol="COOL", thesis="too short")
    assert any("thin" in r for r in v.reasons)
