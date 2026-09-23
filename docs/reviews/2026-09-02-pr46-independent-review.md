# Independent review — PR #46 Reachy motion vocabulary

Date: 2026-09-02
Reviewer: Hermes
Target: `6d47c9c7cb4ba259e025a330bd10d4a1baeb2e93`
Unique-diff verdict: **PASS**
Exact stacked-head delivery state: **NEEDS_FIX — inherited blockers**

## Unique-diff assessment

No blocking defect was found in the motion vocabulary added after PR #45. The implementation keeps motion subordinate to semantic state:

- wake, acknowledgment, phrase, outcome, and idle beats are bounded overlays rather than new policy states;
- repeated beats replace rather than stack;
- asleep states clear beats;
- reduced-motion disables the beat layer;
- phrase boundaries do not rewrite screen-reader status or consume expression-journal receipts;
- action beats derive from actual action-result state;
- the missing live head-drop spring step is repaired.

The power-off browser inspection loaded the real companion route and WebGL scene with no microphone or cloud socket. The final stacked tree passed `1,252` backend tests and the expression state machine passed `48/48`; exact-head remote CI succeeded. PR #40's Rust/Tauri tree passed `16` tests and is unchanged by this PR.

## Remaining gates

- The exact head includes the unresolved #40 power-off/wake-tail defects, #43 gratitude-ending defect, and #45 My Day date-grounding defect. It must not merge as a final tree until those ancestors are repaired and integrated.
- Motion quality is a product/human gate: compare clips in the living-room layout, confirm reduced-motion behavior, and later compare against the physical Reachy Mini. Automated readouts establish bounded mechanics, not that the movement feels right.

Review modified no implementation files.
