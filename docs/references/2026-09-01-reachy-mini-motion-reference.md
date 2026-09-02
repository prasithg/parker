# Reachy Mini motion and expression reference for Parker

Date: 2026-09-01

Status: source-backed design input; no third-party assets or trajectories imported

## Decision

Parker's virtual Reachy should borrow the physical robot's motion vocabulary, not merely its silhouette. The physical and virtual renderers should eventually consume the same small semantic motion contract.

Official evidence establishes the useful vocabulary:

- Reachy Mini has a six-degree-of-freedom head, whole-body yaw, and two independently actuated antennas.
- The SDK distinguishes smooth, duration-based gestures from real-time tracking loops.
- The official conversation app layers primary recorded gestures with breathing and speech-reactive wobble instead of letting every subsystem command the robot directly.
- Pollen's curated emotions library stores head pose, antenna, and body-yaw trajectories at 50 Hz and includes attentive, thoughtful, inquiring, understanding, grateful, welcoming, sleep, success, confused, and related moves.

Do not copy motion/audio files into Parker until their exact asset license and attribution obligations are verified. This document extracts interaction principles only.

## Parker state-to-motion vocabulary

### Powered off

- No idle sway, gaze, breathing, or antenna motion.
- Head/body settle into a stable physical rest pose.
- The screen and future hardware must both look unmistakably unavailable.

### Powered on, dormant

- Use a recognizable sleep/rest silhouette: head lowered and body visually compressed toward the roughly shorter sleep posture described by the official hardware material.
- Antennas relax asymmetrically rather than remaining alert.
- No ambient-speech reaction. Only an actual wake detection may trigger the wake animation.
- Very low-rate breathing is acceptable only if it does not make dormancy look like active listening.

### Wake detected

Use a short staged beat rather than jumping to listening:

1. anticipation: a small downward compression;
2. primary action: head rises and orients;
3. secondary action: antennas perk with a slight delay;
4. settle: one overshoot, then the attentive pose.

The chirp/greeting and visual pop should feel synchronized. Repeated wake events must not stack animations.

### Listening / hearing

- Start from the official `attentive` idea: orient toward the speaker and encourage continuation.
- Use small nods, slight head lean, and occasional antenna acknowledgment—never continuous nervous motion.
- Distinguish `listening` from `hearing`: listening is calm availability; hearing adds a modest orientation/engagement cue from real VAD/mic evidence.
- Do not imply face tracking until an actual source-location or vision signal exists.

### Thinking

- Borrow from `thoughtful`/`inquiring`: modest upward or sideward gaze, asymmetric antenna pose, reduced motion amplitude.
- One held pose is more legible than a busy looping spinner animation.
- A background lookup adds a separate, recognizable antenna rhythm while preserving the main thinking pose.

### Talking

- Keep the head's primary conversational pose stable.
- Layer low-amplitude speech-reactive wobble from actually played audio energy.
- Do not drive every head/antenna channel from raw waveform amplitude; that reads as vibration rather than expression.
- Sentence/phrase boundaries may trigger small nods or antenna punctuation, but only from real playback state.

### Waiting for spoken confirmation

- Hold an attentive, still pose with clear focus.
- Do not nod yes or celebrate before the deterministic action result exists.
- `executed` may use a brief `success`-like beat only after the real pipeline reports execution.
- `failed`, `expired`, and `cancelled` need distinct restrained beats; avoid theatrical sadness.

### Confused / repair

- Borrow from `confused` or `inquiring`, not alarm/fear.
- Hold long enough for the person to understand that Parker needs clarification.
- The gesture must survive until the choice/confirmation state resolves, not merely until speech playback ends.

### Closing and return to dormancy

- A soft closer can use a small grateful/welcoming beat.
- Transition through an explicit wind-down, then the same deterministic dormant pose.
- If the user speaks during the goodbye, cancel the sleep transition and return to listening.

## Motion principles for Fable

1. **Layer motion.** Primary semantic gesture, low-amplitude breathing, speech wobble, and gaze are separate layers with explicit priority.
2. **One owner per degree of freedom.** Blend through a motion controller; do not let voice energy, idle code, and an emotion animation overwrite the same transform independently.
3. **Use minimum-jerk/ease curves for normal interaction.** Reserve cartoon overshoot for wake, success, surprise, and similarly short beats.
4. **Use asymmetry sparingly.** Slightly different antennas and small head rolls feel alive; constant symmetric flapping feels mechanical.
5. **Preserve rests.** Stillness is part of expression. A robot that moves continuously cannot communicate state changes.
6. **Cancel safely.** Barge-in, power off, session end, and a replacement gesture must stop or blend out the current motion without snapping through unsafe/intermediate poses.
7. **Keep virtual/physical parity.** Semantic states may map differently, but both renderers should receive the same event names and timing ownership.
8. **Test against recordings, not memory.** Compare state clips side by side with official examples and Pras's physical Reachy Mini before calling a motion faithful.

## Specific references for the next Fable session

- Official Reachy Mini repository and agent/SDK guide: https://github.com/pollen-robotics/reachy_mini
- Official Reachy Mini documentation: https://huggingface.co/docs/reachy_mini
- Official product article and physical proportions/sleep height: https://huggingface.co/blog/reachy-mini
- Official emotions library and motion names: https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library
- Official conversation app: https://github.com/pollen-robotics/reachy_mini_conversation_app
- Official conversation app pattern: primary queued moves layered with breathing and speech-reactive wobble; head tracking is an explicit tool/signal rather than an invented animation.

## Acceptance evidence for an expressiveness pass

- Capture the same semantic states from the virtual renderer and the physical Reachy Mini: dormant, wake, listening, hearing, thinking, search, talking, confirmation, executed, failed, and closing.
- Review them as clips, not single screenshots.
- Verify that power-off is motionless and dormant does not react to ambient speech.
- Verify wake and sleep are cancellable and do not stack.
- Verify reduced-motion mode keeps state legible without decorative loops.
- Record which official reference inspired each motion; do not claim trajectory-level fidelity unless Parker actually uses a licensed trajectory.

## Deliberate limits

This reference does not authorize camera tracking, physical motor control, importing Pollen datasets, or expanding the current PR before correctness gates. It is source material for a later bounded expressiveness slice after CI, power authority, wake false-positive, and session-end contracts are closed.
