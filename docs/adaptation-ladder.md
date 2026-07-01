# Per-user adaptation ladder (learning flywheel v0)

Parker's "Learn" step, deliberately smallest-first. Every repair exchange
is a naturally labeled example: what ASR heard (primary + alternate
hypotheses), what Parker offered, and what the user confirmed. That pair
— degraded input → confirmed intent — is the flywheel's fuel.

## What exists now (v0)

1. **Repair-event capture** (`app/conversation/repair_events.py`):
   consent-gated (`REPAIR_EVENT_CAPTURE_CONSENTED`, default off — pinned
   off by test), local SQLite only, transcript-level text only. Records
   hypotheses, offered choices, the selection, and rejections
   (none-of-these is signal too). Never audio.
2. **Personal lexicon** (`PERSONAL_LEXICON`): comma-separated names and
   everyday words, injected as the local Whisper initial prompt
   (`lexicon_initial_prompt()`), and measurable via the real-audio
   harness (`--initial-prompt`). The cheapest adaptation rung: family
   names like "Sarah" stop being erased before repair ever runs.
3. **N-best repair choices** (`probe_direct_intent`): alternate ASR
   hypotheses become evidence-based repair choices carrying their parsed
   recipient/subject. Alternates are safety-screened and never routed
   directly.

## The ladder up (not built; build only when the rung below saturates)

4. **Lexicon from data**: mine repair events for words that keep
   appearing in confirmed intents but not in ASR output → suggest lexicon
   additions to the family administrator (human approves; nothing
   self-modifies).
5. **Few-shot repair exemplars**: inject this user's past
   (degraded → confirmed) pairs into the model-driven repair-candidate
   prompt.
6. **Fine-tune corpus**: once volume exists (hundreds of consented
   events + pilot recordings), a per-user ASR or repair fine-tune.
   Requires its own consent conversation — training is a different use
   than operating.

## Measurement

The real-audio harness reports intent recovery with/without repair and
with/without n-best; the pilot recording protocol
([pilot-recording-protocol.md](pilot-recording-protocol.md)) supplies the
speaker-specific test set. A rung earns its place only if the harness
shows a delta on those clips.
