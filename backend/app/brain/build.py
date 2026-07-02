"""Brain selection: which conversational brain answers this session.

One decision, family-administered through config, applied at every entry
point (server, repl, talk loops):

- ``PARKER_OPENCLAW_GATEWAY_URL`` set → the OpenClaw agent is the brain,
  wrapped in ``FallbackBrain`` so a down gateway degrades to the Claude
  adapter (when ``ANTHROPIC_API_KEY`` is set) or an honest spoken notice —
  the voice loop never dies with the gateway.
- Otherwise → the Claude adapter when a key exists, or ``None`` and the
  deterministic answer stub. Zero-config behavior is unchanged.
"""

from __future__ import annotations

from typing import Optional

from app.brain.adapter import BrainAdapter
from app.brain.claude import build_brain_adapter as build_claude_brain
from app.brain.openclaw import FallbackBrain, OpenClawBrainAdapter, build_openclaw_gateway


def build_brain_adapter() -> Optional[BrainAdapter]:
    """The configured brain for this process, or None for the stub."""

    claude_brain = build_claude_brain()
    gateway = build_openclaw_gateway()
    if gateway is None:
        return claude_brain
    return FallbackBrain(OpenClawBrainAdapter(gateway), fallback=claude_brain)
