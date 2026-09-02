# Wake soak 2026-09-01 (synthesized ambient TV + recall matrix)

Synthesized speech only (macOS `say`), real local faster-whisper, the real WakeDetector in browser-sized frames.
This is release evidence for CPU/false-wake behaviour on TV-like speech, NOT a substitute for the real-room evening gate with Dad's voice.

## Ambient-TV soak

- audio: 4.0 min across 5 voices, Parker-adjacent vocabulary throughout
- inferences: 312 (78.0/min of audio)
- CPU: 132.85 s per audio minute = 221% of one core in real time
- inference latency: p50 389.8 ms, p95 876.8 ms, max 7139.0 ms
- false wakes: 0
- hops skipped by the adaptive gate: 0
- config: model=tiny.en threads=auto hop=0.7s relative_gate=0.0

## Recall matrix (must wake)

| phrase | voice | wpm | woke | heard (wake window) | tail after wake (lane) |
|---|---|---|---|---|---|
| hey parker | Samantha | 175 | yes | Hey Parker. |  |
| hey parker | Samantha | 120 | yes | Hey Parker. |  |
| hey parker | Daniel | 175 | yes | Hey Parker. |  |
| hey parker | Daniel | 120 | yes | Hey Parker! |  |
| hey parker | Fred | 175 | yes | Hey Parker! |  |
| hey parker | Fred | 120 | yes | Hey Parker! |  |
| hey, parker. | Samantha | 175 | yes | Hey, Parker. |  |
| hey, parker. | Samantha | 120 | yes | Hey, Parker. |  |
| hey, parker. | Daniel | 175 | yes | Hey, Parker. |  |
| hey, parker. | Daniel | 120 | yes | Hey, Parker. |  |
| hey, parker. | Fred | 175 | yes | Hey, Parker. |  |
| hey, parker. | Fred | 120 | yes | Hey Parker. |  |
| hey... parker | Samantha | 175 | yes | Hey, Parker. |  |
| hey... parker | Samantha | 120 | yes | Hey, Parker. |  |
| hey... parker | Daniel | 175 | yes | Hey, Parker. |  |
| hey... parker | Daniel | 120 | yes | Hey, Parker. |  |
| hey... parker | Fred | 175 | yes | Hey, Parker. |  |
| hey... parker | Fred | 120 | yes | Hey Parker. |  |
| hey parka | Samantha | 175 | yes | Hey, Parker. |  |
| hey parka | Samantha | 120 | yes | Hey, Parker. |  |
| hey parka | Daniel | 175 | yes | Hey Parker. |  |
| hey parka | Daniel | 120 | yes | Hey Parker! |  |
| hey parka | Fred | 175 | yes | Hey Parker! |  |
| hey parka | Fred | 120 | yes | Hey, Parker. |  |
| hey par ker | Samantha | 175 | yes | Hey Parker. |  |
| hey par ker | Samantha | 120 | yes | Hey Parker. |  |
| hey par ker | Daniel | 175 | yes | Hey Parker! |  |
| hey par ker | Daniel | 120 | yes | Hey Parker! |  |
| hey par ker | Fred | 175 | yes | Hey Parker! |  |
| hey par ker | Fred | 120 | yes | Hey Parker! |  |
| hi parker | Samantha | 175 | yes | Hi Parker. |  |
| hi parker | Samantha | 120 | yes | Hi Parker. |  |
| hi parker | Daniel | 175 | yes | Hi Parker. |  |
| hi parker | Daniel | 120 | yes | Hi Parker. |  |
| hi parker | Fred | 175 | yes | Hi Parker. |  |
| hi parker | Fred | 120 | yes | Hi Parker. |  |
| hey parker, can you help me | Samantha | 175 | yes | Hey Parker, can- | Can you help me? |
| hey parker, can you help me | Samantha | 120 | yes | Hey Parker. | Can you help me? |
| hey parker, can you help me | Daniel | 175 | yes | Hey Parker! | Can you help me? |
| hey parker, can you help me | Daniel | 120 | yes | Hey Parker! | Can you help me? |
| hey parker, can you help me | Fred | 175 | yes | Hey Parker! | Can you help me? |
| hey parker, can you help me | Fred | 120 | yes | Hey Parker! | Can you help me? |
| um, hey parker | Samantha | 175 | yes | Um, hey Parker. |  |
| um, hey parker | Samantha | 120 | yes | Um, hey Parker. |  |
| um, hey parker | Daniel | 175 | yes | Um, hey Parker. |  |
| um, hey parker | Daniel | 120 | yes | Um, hey Parker. |  |
| um, hey parker | Fred | 175 | yes | Um, hey Parker. |  |
| um, hey parker | Fred | 120 | yes | Um, hey Parker. |  |

Recall: 48/48.

## Over the TV (voice mixed into TV audio; voice/TV RMS ratio = SNR)

| phrase | voice | SNR dB | woke | heard |
|---|---|---|---|---|
| hey parker | Samantha | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker | Daniel | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker, can you help me | Samantha | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker, can you help me | Daniel | +0 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker | Samantha | -6 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker | Daniel | -6 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker, can you help me | Samantha | -6 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker, can you help me | Daniel | -6 | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |

Over-TV recall: 0/8.

## Confusables (reported, not gated)

| phrase | voice | woke | heard |
|---|---|---|---|
| hey darker | Samantha | yes | Hey Darker. |
| hey darker | Daniel | no |  |
| hey marker | Samantha | yes | Hey Marker. |
| hey marker | Daniel | yes | Hey, Marker. |
| hey barker | Samantha | yes | Hey Barker. |
| hey barker | Daniel | yes | Hey, Barker. |
| hey packer | Samantha | yes | Hey Packer. |
| hey packer | Daniel | no |  |
| hey parked | Samantha | yes | Hey, parked. |
| hey parked | Daniel | no |  |
| a parker | Samantha | no |  |
| a parker | Daniel | no |  |
| hey partner | Samantha | no |  |
| hey partner | Daniel | no |  |
| hey, the parking lot is full | Samantha | no |  |
| hey, the parking lot is full | Daniel | no |  |
| peter parker was here | Samantha | no |  |
| peter parker was here | Daniel | no |  |
| the parker brothers game | Samantha | no |  |
| the parker brothers game | Daniel | no |  |

Gate: PASS (misses=0, false wakes in soak=0).
