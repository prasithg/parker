"""Guard repo architecture docs against stale v0 status claims."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
ARCHITECTURE = REPO / "docs/architecture.md"


STALE_ARCHITECTURE_PHRASES = {
    "needs voice-interface generalization": "typed/audio/live voice input ladder now exists",
    "repair-choice generation not yet built": "repair choices now exist with model opt-in and deterministic fallback",
    "v0 executes reversible reminders only": "v0 also queues confirmed family messages to a local-only outbox",
    "no non-response trigger yet": "non-response escalation candidates are implemented as review-only candidates",
    "eval scaffold covers one task class": "repo now has task-taxonomy, interactivity, demo-trace, and degraded-input replay evals",
    "conversation/prompts.py` still says this": "prompts were reframed to Parker and must not be cited as stale",
}


CURRENT_V0_ARCHITECTURE_NEEDLES = {
    "`backend/app/conversation/textloop.py`": "typed/replay interaction loop",
    "`backend/app/conversation/repair.py`": "repair-choice generation seam",
    "model-driven repair choices": "opt-in LLM repair behavior",
    "deterministic fallback": "zero-config repair fallback",
    "family messages queue to the local outbox": "local-only family message action",
    "two-human gate": "patient confirm plus caregiver approve",
    "non-response escalation candidates": "review-only follow-up/escalation seam",
    "24 synthetic fixtures": "current task-taxonomy eval size",
    "interactivity trace eval": "grant-facing interactivity evaluator",
    "degraded-input replay": "grant-facing transcript repair metric",
}


def test_architecture_doc_has_no_stale_v0_status_claims():
    text = ARCHITECTURE.read_text()

    for phrase, replacement in STALE_ARCHITECTURE_PHRASES.items():
        assert phrase not in text, f"replace stale architecture claim {phrase!r} with current state: {replacement}"


def test_architecture_doc_tracks_current_pilot_ready_surfaces():
    text = ARCHITECTURE.read_text()

    for needle, reason in CURRENT_V0_ARCHITECTURE_NEEDLES.items():
        assert needle in text, f"architecture doc should mention {reason}: {needle}"
