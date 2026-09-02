# Wake soak 2026-09-01 (synthesized ambient TV + recall matrix)

Synthesized speech only (macOS `say`), real local faster-whisper, the real WakeDetector in browser-sized frames.
This is release evidence for CPU/false-wake behaviour on TV-like speech, NOT a substitute for the real-room evening gate with Dad's voice.

## Ambient-TV soak

- audio: 0.0 min across 5 voices, Parker-adjacent vocabulary throughout
- inferences: 0 (0.0/min of audio)
- CPU: 6.0 s per audio minute = 10% of one core in real time
- inference latency: p50 0.0 ms, p95 0.0 ms, max 0.0 ms
- false wakes: 0
- hops skipped by the adaptive gate: 0
- config: model=base threads=2 hop=0.7s relative_gate=0.0

## Recall matrix (must wake)

| phrase | voice | wpm | woke | heard (wake window) | tail after wake (lane) |
|---|---|---|---|---|---|

Recall: 1/1.

## Over the TV (voice mixed into TV audio; voice/TV RMS ratio = SNR)

| phrase | voice | SNR dB | woke | heard |
|---|---|---|---|---|
| hey parker | Samantha | +12 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down here-'] |  |
| hey parker | Daniel | +12 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down top.'] |  |
| hey parker, can you help me | Samantha | +12 | yes | The parking garage down here. Hey, parko. |
| hey parker, can you help me | Daniel | +12 | yes | Hey, Parker. Can you help me? |
| hey parker | Samantha | +6 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down here-'] |  |
| hey parker | Daniel | +6 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down top.'] |  |
| hey parker, can you help me | Samantha | +6 | yes | The parking garage down here. Hey Parker. |
| hey parker, can you help me | Daniel | +6 | yes | Hey, Parker. Can you help me? |
| hey parker | Samantha | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown'] |  |
| hey parker | Daniel | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down down.'] |  |
| hey parker, can you help me | Samantha | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker, can you help me | Daniel | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down top.'] |  |

Over-TV recall: 4/12.

## Confusables (reported, not gated)

| phrase | voice | woke | heard |
|---|---|---|---|

Gate: PASS (misses=0, false wakes in soak=0).
