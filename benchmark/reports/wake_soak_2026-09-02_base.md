# Wake soak 2026-09-02 (synthesized ambient TV + recall matrix)

Synthesized speech only (macOS `say`), real local faster-whisper, the real WakeDetector in browser-sized frames.
This is release evidence for CPU/false-wake behaviour on TV-like speech, NOT a substitute for the real-room evening gate with Dad's voice.
Sections that did not run say so; the gate reads only the sections that ran.

- config: model=base threads=auto hop=0.7s relative_gate=0.0

## Ambient-TV soak (gated: false wakes)

- audio: 4.0 min across 5 voices, Parker-adjacent vocabulary throughout
- inferences: 312 (78.0/min of audio)
- CPU: 144.65 s per audio minute = 241% of one core in real time
- inference latency: p50 423.0 ms, p95 500.4 ms, max 1960.8 ms
- false wakes: 1 — Hey, I'm Parker something. Hey.
- hops skipped by the adaptive gate: 0

## Recall matrix (gated: must wake)

| phrase | voice | wpm | woke | heard (wake window) | tail after wake (lane) |
|---|---|---|---|---|---|
| hey parker | Samantha | 175 | yes | Hey Parker! |  |
| hey parker | Samantha | 120 | yes | Hey Parker |  |
| hey parker | Daniel | 175 | yes | Hey Parker |  |
| hey parker | Daniel | 120 | yes | Hey Parker |  |
| hey parker | Fred | 175 | yes | Hey Parker |  |
| hey parker | Fred | 120 | yes | Hey Parker! |  |
| hey, parker. | Samantha | 175 | yes | Hey, Parker |  |
| hey, parker. | Samantha | 120 | yes | Hey, Parker | You |
| hey, parker. | Daniel | 175 | yes | Hey, Parker! |  |
| hey, parker. | Daniel | 120 | yes | Hey, Parker! |  |
| hey, parker. | Fred | 175 | yes | Hey, Parker. |  |
| hey, parker. | Fred | 120 | yes | Hey Parker |  |
| hey... parker | Samantha | 175 | yes | Hey, Parker |  |
| hey... parker | Samantha | 120 | yes | Hey, Parker | You |
| hey... parker | Daniel | 175 | yes | Hey, Parker! |  |
| hey... parker | Daniel | 120 | yes | Hey, Parker! |  |
| hey... parker | Fred | 175 | yes | Hey, Parker. |  |
| hey... parker | Fred | 120 | yes | Hey Parker |  |
| hey parka | Samantha | 175 | yes | Hey, Parker. |  |
| hey parka | Samantha | 120 | yes | Hey Parka |  |
| hey parka | Daniel | 175 | yes | Hey Parker |  |
| hey parka | Daniel | 120 | yes | Hey Parker |  |
| hey parka | Fred | 175 | yes | Hey, Parker. |  |
| hey parka | Fred | 120 | yes | Hey, Parker. |  |
| hey par ker | Samantha | 175 | yes | Hey Parker! |  |
| hey par ker | Samantha | 120 | yes | Hey Parker |  |
| hey par ker | Daniel | 175 | yes | Hey Parker! |  |
| hey par ker | Daniel | 120 | yes | Hey Parker |  |
| hey par ker | Fred | 175 | yes | Hey Parker |  |
| hey par ker | Fred | 120 | yes | Hey Parker |  |
| hi parker | Samantha | 175 | yes | Hi Parker! |  |
| hi parker | Samantha | 120 | yes | Hi Parker |  |
| hi parker | Daniel | 175 | yes | Hi Parker |  |
| hi parker | Daniel | 120 | yes | Hi Parker |  |
| hi parker | Fred | 175 | yes | Hi Parker! |  |
| hi parker | Fred | 120 | yes | Hi, Parker |  |
| hey parker, can you help me | Samantha | 175 | yes | Hey Parker, Ken | and you helped me. |
| hey parker, can you help me | Samantha | 120 | yes | Hey Parker | Can you help me? |
| hey parker, can you help me | Daniel | 175 | yes | Hey Parker | Can you help me? |
| hey parker, can you help me | Daniel | 120 | yes | Hey Parker! | Can you help me? |
| hey parker, can you help me | Fred | 175 | yes | Hey, Parker. | New Help Me |
| hey parker, can you help me | Fred | 120 | yes | Hey, Parker! | You help me |
| um, hey parker | Samantha | 175 | yes | Um, Hey Parker |  |
| um, hey parker | Samantha | 120 | yes | Um, Hey Parker |  |
| um, hey parker | Daniel | 175 | yes | Um, Hey Parker |  |
| um, hey parker | Daniel | 120 | yes | Um, hay parker |  |
| um, hey parker | Fred | 175 | yes | Um, Hey Parker |  |
| um, hey parker | Fred | 120 | yes | Um, Hey Parker |  |

Recall: 48/48.

## Over the TV (reported, not gated; voice mixed into TV audio, SNR = voice RMS / TV RMS)

Each row is labelled with the SNR the mix achieved. The voice gain is bounded; beyond it the TV bed is attenuated (column), so a request is reached without clipping.

| phrase | voice | SNR requested → achieved (dB) | bed attenuated (dB) | clipped | woke | heard |
|---|---|---|---|---|---|---|
| hey parker | Samantha | +12 → +12.0 | 11.9 | 0.00% | yes | Hey Parker |
| hey parker | Daniel | +12 → +12.0 | 14.4 | 0.00% | yes | Hey Parker |
| hey parker, can you help me | Samantha | +12 → +12.0 | 10.5 | 0.00% | yes | The parking garage down here. Hey Parker. |
| hey parker, can you help me | Daniel | +12 → +12.0 | 14.9 | 0.00% | yes | Hey Parker, can you help me? |
| hey parker | Samantha | +6 → +6.0 | 5.9 | 0.05% | yes | The parking garage down here. Hey Parker. |
| hey parker | Daniel | +6 → +6.0 | 8.4 | 0.00% | yes | Hey, Parker. Hey, Parker. |
| hey parker, can you help me | Samantha | +6 → +6.0 | 4.5 | 0.04% | yes | The parking garage down there. Hey, Parker. |
| hey parker, can you help me | Daniel | +6 → +6.0 | 8.9 | 0.01% | yes | Hey Parker, can you help me? |
| hey parker | Samantha | +0 → -0.1 | 0.0 | 0.20% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown.'] |  |
| hey parker | Daniel | +0 → -0.0 | 2.4 | 0.03% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down down.'] |  |
| hey parker, can you help me | Samantha | +0 → -0.0 | 0.0 | 0.09% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown'] |  |
| hey parker, can you help me | Daniel | +0 → -0.0 | 2.9 | 0.02% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage down top.'] |  |
| hey parker | Samantha | -6 → -6.0 | 0.0 | 0.00% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown'] |  |
| hey parker | Daniel | -6 → -6.0 | 0.0 | 0.00% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown'] |  |
| hey parker, can you help me | Samantha | -6 → -6.0 | 0.0 | 0.00% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown'] |  |
| hey parker, can you help me | Daniel | -6 → -6.0 | 0.0 | 0.01% | NO — ['Good evening.', 'Good evening, the parking garage.', 'Good evening, the parking garage downtown'] |  |

Over-TV recall: 8/16.
- achieved +12.0 dB: 4/4
- achieved +6.0 dB: 4/4
- achieved -0.0 dB: 0/3
- achieved -0.1 dB: 0/1
- achieved -6.0 dB: 0/4

## Confusables (reported, not gated)

| phrase | voice | woke | heard |
|---|---|---|---|
| hey darker | Samantha | yes | Hey Darker |
| hey darker | Daniel | yes | Hey darker |
| hey marker | Samantha | yes | Hey Marker |
| hey marker | Daniel | yes | Hey Marker |
| hey barker | Samantha | yes | Hey Barker |
| hey barker | Daniel | yes | Hey Barker |
| hey packer | Samantha | yes | Hey Packer |
| hey packer | Daniel | yes | Hey Packer! |
| hey parked | Samantha | yes | Hey Parked |
| hey parked | Daniel | yes | Hey Parked. |
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

Extra wakes: 10/20.

## Paused greeting (real silence)

Parts synthesized separately and joined with zeros. Positives must wake and the 10 s stale greeting must stay quiet (gated); the other negatives are reported.

| phrase | voice | wpm | kind | woke | latch_s | heard |
|---|---|---|---|---|---|---|
| hey …3.2 s… parker | Samantha | 175 | positive | yes | 2.3 | Parker |
| hey …3.2 s… parker | Samantha | 120 | positive | yes | 1.5 | Parker |
| hey …3.2 s… parker | Daniel | 175 | positive | yes | 2.3 | Parker Parker |
| hey …3.2 s… parker | Daniel | 120 | positive | yes | 3.1 | Parker |
| hey …3.2 s… parker | Fred | 175 | positive | yes | 3.1 | Parker |
| hey …3.2 s… parker | Fred | 120 | positive | NO — heard ['Hey', 'Hey', 'Hey', 'Hey.'] |  |  |
| hey …4.0 s… parker, can you help me | Samantha | 175 | positive | yes | 3.1 | Parker |
| hey …4.0 s… parker, can you help me | Samantha | 120 | positive | yes | 2.3 | Parker |
| hey …4.0 s… parker, can you help me | Daniel | 175 | positive | yes | 3.8 | Parker, can you hear me? |
| hey …4.0 s… parker, can you help me | Daniel | 120 | positive | yes | 3.8 | Parker, can you... |
| hey …4.0 s… parker, can you help me | Fred | 175 | positive | NO — heard ['Hey', 'Hey', 'Hey', ''] |  |  |
| hey …4.0 s… parker, can you help me | Fred | 120 | positive | yes | 3.1 | Tarker |
| hi …3.2 s… parker | Samantha | 175 | positive | yes | 2.3 | Parker |
| hi …3.2 s… parker | Samantha | 120 | positive | yes | 2.3 | Parker |
| hi …3.2 s… parker | Daniel | 175 | positive | yes | 2.3 | Parker |
| hi …3.2 s… parker | Daniel | 120 | positive | yes | 3.1 | Parker |
| hi …3.2 s… parker | Fred | 175 | positive | yes | 3.1 | Parker |
| hi …3.2 s… parker | Fred | 120 | positive | NO — heard ['Huh?', 'Hi', 'Hi', 'Bye.'] |  |  |
| um, hey …3.2 s… parker | Samantha | 175 | positive | yes | 2.3 | Parker |
| um, hey …3.2 s… parker | Samantha | 120 | positive | yes | 2.3 | Harker |
| um, hey …3.2 s… parker | Daniel | 175 | positive | yes | 2.3 | Parker |
| um, hey …3.2 s… parker | Daniel | 120 | positive | yes | 3.1 | Parker |
| um, hey …3.2 s… parker | Fred | 175 | positive | NO — heard ['Oh', "I'm a", "I'm a", 'Okay'] |  |  |
| um, hey …3.2 s… parker | Fred | 120 | positive | NO — heard ['Oh', "I'm Okay", 'Um, hey.', 'Okay'] |  |  |
| hey …10.0 s… parker | Samantha | 175 | stale | no |  |  |
| hey …10.0 s… parker | Samantha | 120 | stale | no |  |  |
| hey …10.0 s… parker | Daniel | 175 | stale | no |  |  |
| hey …10.0 s… parker | Daniel | 120 | stale | no |  |  |
| hey …10.0 s… parker | Fred | 175 | stale | no |  |  |
| hey …10.0 s… parker | Fred | 120 | stale | no |  |  |
| hey …1.0 s… I'm parking the car …2.6 s… parker | Samantha | 175 | reported | no |  |  |
| hey …1.0 s… I'm parking the car …2.6 s… parker | Samantha | 120 | reported | no |  |  |
| hey …1.0 s… I'm parking the car …2.6 s… parker | Daniel | 175 | reported | no |  |  |
| hey …1.0 s… I'm parking the car …2.6 s… parker | Daniel | 120 | reported | no |  |  |
| hey …1.0 s… I'm parking the car …2.6 s… parker | Fred | 175 | reported | no |  |  |
| hey …1.0 s… I'm parking the car …2.6 s… parker | Fred | 120 | reported | no |  |  |
| a …3.2 s… parker | Samantha | 175 | reported | YES — extra wake | 2.3 | Parker |
| a …3.2 s… parker | Samantha | 120 | reported | YES — extra wake | 2.3 | Parker |
| a …3.2 s… parker | Daniel | 175 | reported | no |  |  |
| a …3.2 s… parker | Daniel | 120 | reported | YES — extra wake | 3.1 | Parker |
| a …3.2 s… parker | Fred | 175 | reported | YES — extra wake | 2.3 | Tarker |
| a …3.2 s… parker | Fred | 120 | reported | no |  |  |

Paused positives: 19/24; stale quiet: 6/6; reported negatives that woke: 4/12.

Gate: FAIL (soak 4.0 min: 1 false wake; recall 48/48; paused 19/24, stale quiet 6/6) — over-TV 8/16 reported, not gated; confusables 10/20 extra wakes reported, not gated.
