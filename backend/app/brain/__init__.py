"""Parker's pluggable brain.

Parker itself is the brainstem: ear/mouth, understanding/repair, and the
policy broker. The *brain* — the thing that can actually converse — is
pluggable behind the ``BrainAdapter`` contract in ``adapter.py``. The v0
brain is Claude over the direct Anthropic API (``claude.py``); future
brains (OpenClaw/Hermes, realtime speech models) implement the same
contract. See ``docs/brain-adapters.md``.

The brain may TALK freely on the informational tier; it may ACT only by
proposing actions that re-enter Parker's deterministic capture → confirm
→ stage pipeline. It never sees an utterance the safety guards refused.
"""
