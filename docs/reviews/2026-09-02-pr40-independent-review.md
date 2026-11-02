# Independent review — PR #40 companion foundation

Date: 2026-09-02
Reviewer: Hermes, with three fresh read-only review lenses
Behavioral target: `a454b2706f085565ada12b2a38ed62231077bb53`
Ledger-only PR head inspected: `1255d22093eb5d83b69c867c0ed44bd50a043b96`
Verdict: **NEEDS_FIX**

## Blocking findings

### 1. Power-off does not stop all ongoing cloud processing

`RealtimeBridge._shutdown()` cancels pump/worker tasks and performs queued persistence before closing the upstream realtime socket. A blocked-finalizer probe kept the fake upstream open until finalization ended: `finalize_start=0.003s`, `finalize_end=1.208s`, `upstream_closed=1.209s`.

Search/context workers run synchronous functions through `_tracked_thread`. Cancelling the asyncio task abandons an already-running thread. `test_lookup_cancelled_by_close_never_reports_a_late_result` proves only that the result is not delivered; it releases the still-running worker after the websocket closes.

This violates the contract that off means no listening, streaming, processing, or response. Revoke input and cancel/close the upstream connection first; every external worker needs a real stop contract. Persistence may drain afterward and must not hold the privacy boundary open. Pin upstream and provider cancellation while their finalizers are deliberately blocked.

### 2. Same-breath speech is still lost, and its surviving tail is promoted to system authority

When realtime opens, the page immediately freezes the current `{type: "hello", tail}` and closes the wake lane. A delayed local-ASR tail arriving after that is ignored, and audio already sent to the wake lane is not replayed to realtime. A fresh actual-page reproduction produced `hello.tail="can you"` and dropped the delayed `help me with the tv`. Existing Node coverage sends both tail frames before realtime opens and misses the race.

The browser also replaces `wake.tail` with each rolling 2.4-second transcription window while the lane may remain open for 3 seconds; a later sliding window can erase earlier words.

Finally, `_greeting_instruction()` interpolates this user/ambient transcription into a message sent with `role: system`. The content must remain user/untrusted input.

Make the handoff ordered: acknowledge/drain already-sent wake audio to a final tail, or buffer/replay the relevant PCM into realtime. Preserve the transcript monotonically, send it as user content, and test realtime opening before delayed tail delivery.

### 3. Parkinsonian greeting pauses beyond the PCM window miss wake

The detector retains only 2.4 seconds of PCM and has no bounded greeting memory across ASR windows. A real faster-whisper reproduction woke for `hey parker` but missed `hey` + 3.2 seconds of silence + `parker`; the soak's `hey... parker` is punctuation in one synthesized utterance, not a temporal-pause test.

Add a bounded greeting latch across windows, reset by timeout or intervening lexical speech. Pin real temporal PCM above the current window while keeping bare `a` negative and Parker-like variants permissive.

### 4. Source citations overwrite scheduled audio nodes

`companion_ui.py` uses `live.sources` both for scheduled `AudioBufferSourceNode`s and citation objects. After `audio → sources → clear`, `flushLivePlayback()` tries `stop()` on citations and has lost the real audio nodes. The actual page runtime left scheduled audio unstopped. Old speech can therefore survive interruption/guard clearing, and citation-only state can count as buffered audio.

Split playback nodes from source citations and add a browser-runtime regression combining audio, sources, and clear/guard.

### 5. Realistic missing-local-model failures do not reach the fail-closed UI

`ConverseStore._warm_transcriber()` catches only `RuntimeError`. Missing cached model weights/offline resolution can raise `LocalEntryNotFoundError` (`FileNotFoundError`/`OSError` lineage), which escapes instead of returning `None`. The wake socket then takes the generic retry/error path rather than sending `unavailable`; the page does not execute the promised power-off transition. No cloud fallback opens, but engine/UI state is false.

Normalize model-initialization failures to unavailable, surface repeated fatal inference errors, and add route-to-UI tests for `OSError`/missing-cache failures.

### 6. Microphone denial makes “Try again” perform the opposite action

After a successful engine claim, `getUserMedia()` denial leaves persisted engine power on but renders `error`, `aria-checked="false"`, and label `Try again`. Internally `switchedOn()` treats `error` as on, so activating the control powers off; a second activation is required to retry.

Release the failed claim and return to true off, or make one `Try again` activation release/reclaim/retry. Pin the actual control semantics and persisted state.

### 7. Reduced-motion behavior is incomplete

The media preference stops the WebGL loop, but CSS still animates the dormant power lamp and retains nonessential card/control transitions and transforms. Disable nonessential animation, transitions, and transforms under `prefers-reduced-motion`; test both WebGL and CSS surfaces.

### 8. Packaged and wake evidence can false-pass or overclaim

The packaged probe accepts any pre-existing `.app` without revision/build provenance, while docs order the probe before the build. Its orphan check searches `PARKER_HOME` in process command lines even though that value is an environment variable, and absent engine routes cannot prove the OS microphone never opened. The probe does prove that a bundle fetched `/parker/converse` and posted a WebGL receipt; it does not prove that bundle is this revision, pixels are correct, the sidecar is gone, or TCC never opened capture.

The wake harness also treats skipped sections as data: a skipped recall becomes `1/1`, zero-minute soaks emit CPU-per-minute figures, and a partial over-TV run can say `Gate: PASS` while over-TV recall is 4/12. Voice gain is capped and mixed with saturating PCM, but requested SNR is reported as achieved SNR. Fresh reproduction found `+12 dB` labels at about `+5.76/+3.21 dB` for the short phrase and `+7.27/+3.12 dB` for the long phrase across two voices; some requested `+6` and `+12` mixes are identical after the cap. The ledger also contradicts itself on whether the adaptive gate is production-default or opt-in.

Require exact build/SHA provenance, robust child-process identity, and a truthful limit on what the packaged probe establishes. Represent skipped wake sections as `not_run`/null; never global-PASS partial runs; record achieved SNR and regenerate reports/ledger. The 48/48 padded synthetic recall remains valid only as that narrow run, with no false-wake claim.

## Required human/device gates

The synthetic evidence is not Dad's speech or room. Because #40 changes power and wake behavior, it cannot merge until the packaged real-mic/TCC transition, Dad-like effortful wake recall, ambient TV behavior, same-breath request, stop/barge-in, and true power revocation are recorded. VoiceOver order and no-WebGL/reduced-motion behavior also remain human/runtime gates.

## What passed

- Remote exact-SHA CI at `a454b27`: `Backend tests and release evals` succeeded.
- Clean backend suite: `1,234 passed`, two existing deprecation warnings.
- Concurrency/power deck repeated five times: `30/30` each run.
- Rust/Tauri: `16 passed`.
- Release-readiness evaluator passed against the reports' own date (`2026-08-31`).
- Fresh focused lenses: wake/ASR `190 passed`; companion/runtime `31/31` Node + `42` pytest; `git diff --check` passed.
- Engine-owned single-listener power, bounded reconnect, dormant no-cloud behavior, live-region separation, packaged companion routing, and per-test SQLite isolation are material improvements.

## Non-blocking evidence risks to carry into reconciliation

- Test SQLite uses WAL, `synchronous=OFF`, and a 30-second busy timeout; production uses defaults. Keep fixture isolation, but add at least one production-shaped contention test or state the limit.
- The scene receipt marks itself reported before confirming a receipt session exists, so module/session startup ordering can lose the only WebGL receipt.
- The release evaluator permits `--as-of` with `--write-report`, enabling a report dated today from an overridden evidence date. Forbid the combination or date the output with `as_of`.

## Delivery caveat

The current PR head is documentation-only beyond `a454b27`, but no check is attached to `1255d22`. Live #40 is also divergent from its moved #37 base and live `main`; use explicit stack reconciliation and a final semantic-union audit rather than UI conflict resolution.

Review modified no implementation files.
