# Wake soak 2026-09-02 (synthesized ambient TV + recall matrix)

Synthesized speech only (macOS `say`), real local faster-whisper, the real WakeDetector in browser-sized frames.
This is release evidence for CPU/false-wake behaviour on TV-like speech, NOT a substitute for the real-room evening gate with Dad's voice.

## Ambient-TV soak

- audio: 4.0 min across 5 voices, Parker-adjacent vocabulary throughout
- inferences: 312 (78.0/min of audio)
- CPU: 177.47 s per audio minute = 296% of one core in real time
- inference latency: p50 435.9 ms, p95 884.4 ms, max 5501.9 ms
- false wakes: 1 — Hey, I'm Parker something. Hey.
- hops skipped by the adaptive gate: 0
- config: model=base threads=auto hop=0.7s relative_gate=0.0

## Recall matrix (must wake)

| phrase | voice | wpm | woke | heard (wake window) | tail after wake (lane) |
|---|---|---|---|---|---|

Recall: 1/1.

## Over the TV (voice mixed into TV audio; voice/TV RMS ratio = SNR)

| phrase | voice | SNR dB | woke | heard |
|---|---|---|---|---|

Over-TV recall: 0/0.

## Confusables (reported, not gated)

| phrase | voice | woke | heard |
|---|---|---|---|

Gate: FAIL (misses=0, false wakes in soak=1).
