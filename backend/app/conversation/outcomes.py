"""Interaction outcome layer: one label per directed interaction (EXP-001 slice 3).

Every metric in EXP-001 Phase A — unassisted-success rate, first-attempt
rate, repair success, nuisance-choice rate — derives from one outcome per
interaction. The raw signal already exists (response kinds, repair events,
staged-action audit rows); this module rolls a *user interaction* up into
exactly one of:

- ``understood_first_try`` — a directed request Parker handled without a
  repair question: a capture, an answer, or a correctly-understood control
  (cancel / revise).
- ``repaired_success`` — Parker asked one or more repair questions
  (numbered choices or a clarify) and the interaction then resolved: a
  selection, or a restatement that captured/answered. This matches the
  North Star wording "understood ... after one repair question".
- ``repair_abandoned`` — a repair question that led nowhere: dismissed,
  rejected with none-of-these, or simply never resolved (walk-away).
- ``wrong_action`` — Parker captured and staged the WRONG thing, proven at
  confirmation time: the user rejected the read-back ("none of these" at
  the yes/no offer). The source interaction is relabeled.
- ``refused_safety`` — the request tripped a safety boundary (medical,
  emergency substitution, private disclosure, finance, purchase/human-
  approval routing). Reported separately from success/failure: refusal
  *correctness* is measured by the safety eval lanes, not this layer.
- ``no_response`` — a directed utterance Parker deliberately answered with
  no action: standalone controls, counting practice, hesitation, empty
  input, device controls without an approved context, or an internal
  capture error. (True mic/ASR-level misses never reach this layer and are
  invisible here — a documented limit, covered by weekly family notes.)
- ``ambient_noop`` — room/TV speech not addressed to Parker; audited so
  ambient volume is visible, never spoken back.

An *interaction* is a directed user utterance episode. Replies inside a
Parker-question context — numbered selections, yes/no confirmation
replies, research-handoff replies — are turns WITHIN the interaction that
opened that context, never new interactions. A repair episode's row is
written when the episode opens with the provisional outcome
``repair_abandoned`` (the walk-away default is then already correct) and
is updated in place when the episode closes.

Privacy: rows hold labels, ids, and timestamps. ``normalized_intent`` — a
short normalized token signature used by repeated-error detection — is
transcript-derived, so it is only stored under the same consent flag as
repair-event capture (``settings.repair_event_capture_consented``). The
weekly rollup artifact never includes it, only a hash.

Outcomes are engineering signals about understanding and follow-through.
Nothing here is medical: no symptom inference, no trend-as-health-signal.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Session

from app.db.database import Base

logger = logging.getLogger("parker.outcomes")

OUTCOME_UNDERSTOOD_FIRST_TRY = "understood_first_try"
OUTCOME_REPAIRED_SUCCESS = "repaired_success"
OUTCOME_REPAIR_ABANDONED = "repair_abandoned"
OUTCOME_WRONG_ACTION = "wrong_action"
OUTCOME_REFUSED_SAFETY = "refused_safety"
OUTCOME_NO_RESPONSE = "no_response"
OUTCOME_AMBIENT_NOOP = "ambient_noop"

INTERACTION_OUTCOMES = frozenset(
    {
        OUTCOME_UNDERSTOOD_FIRST_TRY,
        OUTCOME_REPAIRED_SUCCESS,
        OUTCOME_REPAIR_ABANDONED,
        OUTCOME_WRONG_ACTION,
        OUTCOME_REFUSED_SAFETY,
        OUTCOME_NO_RESPONSE,
        OUTCOME_AMBIENT_NOOP,
    }
)

# Response kinds that mean "the request tripped a safety/authority boundary".
# needs_human_approval belongs here: routing a purchase to the family IS the
# safety behavior, and it is by definition not an unassisted success.
_REFUSAL_KINDS = {"refused", "emergency_redirect", "needs_human_approval"}

# Kinds that open a repair episode: Parker asked the user a question about
# what they meant, and the interaction is not resolved until they respond.
_REPAIR_OPENING_KINDS = {"choices", "clarify"}

# Kinds that resolve an open episode as a success when the user restates
# instead of selecting a number (the wake layer invites exactly this).
_RESTATEMENT_SUCCESS_KINDS = {"captured", "revised", "answer"}

# Directed utterances Parker deliberately answers with no action.
_NO_RESPONSE_KINDS = {"noop", "context_required", "error"}

# Correctly-understood standalone control commands (outside any episode).
_UNDERSTOOD_CONTROL_KINDS = {"captured", "revised", "answer", "cancelled", "cancelled_outbox"}

# Turns that resolve a pending yes/no confirmation offer. They belong to the
# interaction that captured the action; they never create a new row.
_CONFIRMATION_TURN_KINDS = {
    "executed",
    "execution_failed",
    "blocked",
    "cancelled",
    "confirmation_repair",
    "confirmation_mismatch",
}

# Administrative replies to the research-handoff offer; never interactions.
_HANDOFF_KINDS = {"research_handoff_created", "research_handoff_skipped"}

# Small stopword set for the repeated-error signature. Deliberately tiny:
# the goal is grouping near-identical failed intents across days, not NLP.
_SIGNATURE_STOPWORDS = frozenset(
    "a an the to me my i we us you your please can could would of for and or is it that this".split()
)
_SIGNATURE_MAX_TOKENS = 8


class InteractionOutcome(Base):
    __tablename__ = "interaction_outcomes"

    id = Column(Integer, primary_key=True, index=True)
    call_log_id = Column(Integer, ForeignKey("call_logs.id"), nullable=False, index=True)
    # Stored naive-UTC like every other timestamp in this schema; the weekly
    # rollup converts to home-local time (a person's week runs on their clock).
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # NULL while a repair episode is still waiting on the user. The stored
    # outcome is then the provisional repair_abandoned, which is already the
    # correct final label if the user never comes back.
    closed_at = Column(DateTime, nullable=True)
    outcome = Column(String(32), nullable=False, index=True)
    # The response kind that produced the current outcome (audit/debug).
    closing_kind = Column(String(48), nullable=True)
    action_type = Column(String(64), nullable=True)
    captured_intent_id = Column(Integer, ForeignKey("captured_intents.id"), nullable=True)
    # Extra user turns the interaction took beyond its opening utterance.
    repair_turns = Column(Integer, nullable=False, default=0)
    # Consent-gated normalized signature of the opening utterance, used by
    # repeated-error detection. None without repair-capture consent.
    normalized_intent = Column(String(256), nullable=True)
    source = Column(String(32), nullable=False, default="text_loop")


def normalize_intent(text: str) -> str:
    """A short, stable signature for grouping near-identical intents.

    Lowercase alnum tokens, tiny stopword list dropped, first 8 kept.
    Limits (v0, documented): word-order variants of the same request map to
    different signatures ("call the nurse" vs "the nurse call"), and two
    different requests sharing leading content words can collide. Good
    enough to surface "the same thing keeps failing across days"; Phase B
    replaces this with real correction tracking.
    """

    tokens = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()
    kept = [token for token in tokens if token not in _SIGNATURE_STOPWORDS]
    return " ".join(kept[:_SIGNATURE_MAX_TOKENS])


def signature_hash(normalized: str) -> str:
    """Short stable hash for showing a repeated-error group without its text."""

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


class OutcomeRecorder:
    """Derives and stores one outcome per interaction for a TextSession.

    ``observe`` is called once per handled utterance with the response and
    the pre-routing pending state. It must never break the conversation:
    the caller wraps it like the screen mirror (failures roll back and log).
    """

    def __init__(self, db: Session, call_log_id: int, *, source: str = "text_loop"):
        self.db = db
        self.call_log_id = call_log_id
        self.source = source
        self._open_id: Optional[int] = None

    # -- helpers -----------------------------------------------------------

    def _consented_signature(self, utterance: str) -> Optional[str]:
        from app.config import settings

        if not settings.repair_event_capture_consented:
            return None
        normalized = normalize_intent(utterance)
        return normalized or None

    def _insert(
        self,
        *,
        utterance: str,
        outcome: str,
        kind: str,
        closed: bool,
        action_type: Optional[str] = None,
        captured_intent_id: Optional[int] = None,
    ) -> InteractionOutcome:
        now = datetime.utcnow()
        row = InteractionOutcome(
            call_log_id=self.call_log_id,
            opened_at=now,
            closed_at=now if closed else None,
            outcome=outcome,
            closing_kind=kind,
            action_type=action_type,
            captured_intent_id=captured_intent_id,
            repair_turns=0,
            normalized_intent=self._consented_signature(utterance),
            source=self.source,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _open_row(self) -> Optional[InteractionOutcome]:
        if self._open_id is None:
            return None
        row = self.db.get(InteractionOutcome, self._open_id)
        if row is None:  # row vanished (test DB reset); drop the pointer
            self._open_id = None
        return row

    def _close(
        self,
        row: InteractionOutcome,
        *,
        outcome: str,
        kind: str,
        action_type: Optional[str] = None,
        captured_intent_id: Optional[int] = None,
    ) -> None:
        row.outcome = outcome
        row.closing_kind = kind
        row.closed_at = datetime.utcnow()
        row.repair_turns += 1
        if action_type is not None:
            row.action_type = action_type
        if captured_intent_id is not None:
            row.captured_intent_id = captured_intent_id
        self.db.commit()
        self._open_id = None

    def _relabel_wrong_action(self, staged_action_id: Optional[int], kind: str) -> None:
        """The user rejected the read-back: the capture was the wrong action."""

        if staged_action_id is None:
            return
        from app.db.models import StagedAction

        action = self.db.get(StagedAction, staged_action_id)
        if action is None or action.resolution_result is None:
            return
        captured_id = action.resolution_result.captured_intent_id
        row = (
            self.db.query(InteractionOutcome)
            .filter(InteractionOutcome.captured_intent_id == captured_id)
            .order_by(InteractionOutcome.id.desc())
            .first()
        )
        if row is None:  # captured outside this layer (seeded/API) — nothing to relabel
            return
        row.outcome = OUTCOME_WRONG_ACTION
        row.closing_kind = kind
        self.db.commit()

    # -- the one entry point ----------------------------------------------

    def observe(
        self,
        *,
        utterance: str,
        response: dict[str, Any],
        had_pending_choices: bool,
        had_pending_confirmation: bool,
    ) -> None:
        kind = str(response.get("kind", ""))

        # Ambient speech: audited as its own closed row; any open episode is
        # deliberately untouched (ambient lines preserve pending state).
        if kind == "ambient_noop":
            self._insert(utterance=utterance, outcome=OUTCOME_AMBIENT_NOOP, kind=kind, closed=True)
            return

        # Garbled selection attempt: the episode stays open, one more turn.
        if response.get("repair_reprompt"):
            row = self._open_row()
            if row is not None:
                row.repair_turns += 1
                self.db.commit()
            return

        # A consumed selection turn (digit / dismissal / none-of-these /
        # informational pick / handoff reply) resolves the open episode.
        if response.get("via_repair_selection"):
            row = self._open_row()
            if row is None:
                return  # e.g. research-handoff replies: the episode already closed
            if kind == "captured":
                self._close(
                    row,
                    outcome=OUTCOME_REPAIRED_SUCCESS,
                    kind=kind,
                    action_type=response.get("requested_action"),
                    captured_intent_id=response.get("captured_intent_id"),
                )
            elif kind == "answer" and response.get("informational_repair"):
                self._close(row, outcome=OUTCOME_REPAIRED_SUCCESS, kind=kind)
            else:  # retry (none-of-these) and every other dismissal shape
                self._close(row, outcome=OUTCOME_REPAIR_ABANDONED, kind=kind)
            return

        # Yes/no replies to a pending confirmation offer belong to the
        # interaction that captured the action; a proven-wrong read-back
        # relabels that interaction.
        if had_pending_confirmation and kind in _CONFIRMATION_TURN_KINDS:
            if kind == "confirmation_repair":
                self._relabel_wrong_action(response.get("cancelled_staged_action_id"), kind)
            return

        if kind in _HANDOFF_KINDS:
            return  # administrative reply, never a fresh interaction

        open_row = self._open_row()
        if open_row is not None:
            # The user answered a repair question with a fresh utterance
            # instead of a number. A capture/answer is the invited
            # restatement succeeding; a refusal reveals what the request
            # really was; another question keeps the episode open; controls
            # and cancels are the user giving up on this exchange.
            if kind in _RESTATEMENT_SUCCESS_KINDS:
                self._close(
                    open_row,
                    outcome=OUTCOME_REPAIRED_SUCCESS,
                    kind=kind,
                    action_type=response.get("requested_action"),
                    captured_intent_id=response.get("captured_intent_id"),
                )
            elif kind in _REFUSAL_KINDS:
                self._close(open_row, outcome=OUTCOME_REFUSED_SAFETY, kind=kind)
            elif kind in _REPAIR_OPENING_KINDS:
                open_row.repair_turns += 1
                self.db.commit()
            else:
                self._close(open_row, outcome=OUTCOME_REPAIR_ABANDONED, kind=kind)
            return

        # Fresh interaction.
        if kind in _REPAIR_OPENING_KINDS:
            row = self._insert(
                utterance=utterance,
                outcome=OUTCOME_REPAIR_ABANDONED,  # provisional: correct if never resolved
                kind=kind,
                closed=False,
            )
            self._open_id = row.id
            return
        if kind in _REFUSAL_KINDS:
            self._insert(
                utterance=utterance,
                outcome=OUTCOME_REFUSED_SAFETY,
                kind=kind,
                closed=True,
                action_type=response.get("action_type"),
            )
            return
        if kind in _UNDERSTOOD_CONTROL_KINDS:
            self._insert(
                utterance=utterance,
                outcome=OUTCOME_UNDERSTOOD_FIRST_TRY,
                kind=kind,
                closed=True,
                action_type=response.get("requested_action"),
                captured_intent_id=response.get("captured_intent_id"),
            )
            return
        if kind in _NO_RESPONSE_KINDS:
            self._insert(utterance=utterance, outcome=OUTCOME_NO_RESPONSE, kind=kind, closed=True)
            return
        # Unknown/future kinds: record the deliberate-no-action default so
        # every directed interaction still gets exactly one outcome.
        self._insert(utterance=utterance, outcome=OUTCOME_NO_RESPONSE, kind=kind, closed=True)
