"""Repair-event capture: the learning flywheel's v0 primitive.

Every repair exchange is a naturally labeled example: the ASR hypotheses
Parker heard, the interpretations it offered, and the one the user
confirmed. Stored locally (SQLite, this household) they become, in order
of ambition: a personal lexicon to bias ASR, few-shot exemplars for
repair-candidate generation, and eventually a fine-tuning corpus.

Consent contract: nothing is written unless
``settings.repair_event_capture_consented`` is true — default false, set
explicitly by the family administrator. Only transcript-level text is
stored, never audio (audio is deleted after transcription elsewhere, and
this module never sees it).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import Base


class RepairEvent(Base):
    __tablename__ = "repair_events"

    id = Column(Integer, primary_key=True, index=True)
    call_log_id = Column(Integer, ForeignKey("call_logs.id"), nullable=False)
    utterance = Column(Text, nullable=False)  # the degraded primary transcript
    alternates_json = Column(Text, nullable=False, default="[]")  # n-best hypotheses
    offered_choices_json = Column(Text, nullable=False, default="[]")
    selected_position = Column(Integer, nullable=True)
    selected_label = Column(String, nullable=True)
    # None means the user picked none-of-these — rejections are signal too.
    selected_action_type = Column(String, nullable=True)
    captured_intent_id = Column(Integer, ForeignKey("captured_intents.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def record_repair_event(
    db: Session,
    *,
    call_log_id: int,
    utterance: str,
    alternates: list[str],
    choices: list[dict[str, Any]],
    selected_position: Optional[int],
    selected_label: Optional[str],
    selected_action_type: Optional[str],
    captured_intent_id: Optional[int] = None,
) -> Optional[RepairEvent]:
    """Store one repair exchange — or nothing at all without consent."""

    if not settings.repair_event_capture_consented:
        return None
    event = RepairEvent(
        call_log_id=call_log_id,
        utterance=utterance,
        alternates_json=json.dumps(alternates),
        offered_choices_json=json.dumps(
            [{"label": c.get("label"), "action_type": c.get("action_type")} for c in choices]
        ),
        selected_position=selected_position,
        selected_label=selected_label,
        selected_action_type=selected_action_type,
        captured_intent_id=captured_intent_id,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
