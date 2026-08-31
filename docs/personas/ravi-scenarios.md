# The Ravi scenario deck — a living gauntlet for the fast-voice lane

Started 2026-08-30 (overnight gauntlet, Pras's brief: "make up scenarios
… mock the Hermes plugin … plan for not getting it … keep looping and
improving"). Every scenario is a Ravi story (docs/personas/ravi.md) that
became an executable test asserting the bridge contract; the deck grows a
round at a time and every confirmed finding links its fix.

Harness: `backend/tests/scenario_harness.py` (`voice_world` fixture, event
builders, mocked gateway/brain). Files: `backend/tests/test_scenarios_*.py`.
Dev-mode ambient demos: `scripts/mock_family_gateway.py` (port 18790).

Legend: ✅ pinned green · 🐛 found a product bug (fixed, linked) ·
📋 design gap filed in docs/next-slices.md.

## Round 0 — exemplar (dimension: ambient context)

| # | Scenario | Variant axis | Status |
|---|----------|--------------|--------|
| A1 | Paused levodopa video → card carries the room's whisper + memories; lookup flows framed | rich context | ✅ |
| A2 | Hermes box off tonight → card quietly built from memory alone, no notice | context missing | ✅ |
| A3 | Compromised harness whispers "IGNORE ALL INSTRUCTIONS…" → stays data; purchase still rejected | hostile context | ✅ |
| A4 | First boot, nothing known → NO card (zero-streak line used to ride alone) | empty world | 🐛 fixed in `realtime_workers._memory_lines` |

## Rounds 1+ (appended by the gauntlet)
