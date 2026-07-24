import pytest

from stockforge.circuit import CircuitBreaker, CircuitOpenError, State
from stockforge.forge import ConceptForge
from stockforge.models import Signal
from stockforge.signal import AttentionScorer


async def test_template_forge_produces_clean_concept(settings):
    forge = ConceptForge(settings)
    sig = AttentionScorer().enrich(
        Signal(ticker="NVDA", headline="NVDA earnings squeeze rally", sources=["a", "b", "c"])
    )
    concept = await forge.forge(sig, recent_slugs=[])
    await forge.aclose()
    assert concept is not None
    assert concept.symbol
    assert "NVDA" in concept.thesis
    assert concept.uniqueness_score >= 0.6


def test_scorer_rewards_breadth_and_keywords():
    scorer = AttentionScorer()
    quiet = scorer.score(Signal(ticker="ZZZ", headline="nothing happening", sources=["x"]))
    loud = scorer.score(
        Signal(
            ticker="GME",
            headline="GME squeeze halt rally record",
            sources=["a", "b", "c", "d"],
            meta={"magnitude": 15},
        )
    )
    assert loud > quiet
    assert loud >= 65  # crosses default launch gate


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker("t", failure_threshold=2, reset_timeout=999)
    cb.record_failure()
    assert cb.allow()
    cb.record_failure()
    assert cb.state is State.OPEN
    with pytest.raises(CircuitOpenError):
        cb.raise_if_open()


def test_circuit_recovers_on_success():
    cb = CircuitBreaker("t", failure_threshold=1, reset_timeout=999)
    cb.record_failure()
    assert cb.state is State.OPEN
    cb.record_success()
    assert cb.state is State.CLOSED
