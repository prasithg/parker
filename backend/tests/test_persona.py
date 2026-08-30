"""Ravi's seed feeds the context card — and the card drops his dose lines."""

from __future__ import annotations

from app.demo.persona import seed_persona_data
from app.parker import realtime_workers


def test_persona_seed_is_idempotent_and_card_ready(db):
    summary = seed_persona_data(db)
    assert summary["skipped"] is False
    assert summary["medications"] == 2
    assert summary["memories"] == 6

    again = seed_persona_data(db)
    assert again["skipped"] is True

    card = realtime_workers.run_context_worker(lambda: db)
    assert "old Hindi songs" in card.speech
    assert "Alcaraz" in card.speech  # yesterday's live session came back
    assert "unsteady" in card.speech  # the concern line
    assert "25-100 mg" not in card.speech  # dose lines never reach the model
    assert "adherence streak: 3" in card.speech
