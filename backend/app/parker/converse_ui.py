"""Single-file Patient Curiosity Loop page — GET /parker/converse.

The first-user surface from the 2026-08-29 strategy doc: tap Start, take
your time (pauses never cut you off — only your own Done ends the turn),
see what Parker heard, get a brief current answer with its source named on
screen, ask a follow-up, and Stop instantly.

Design contract (pinned by tests):

- Four large controls: Start listening, Done talking, Stop Parker, Try
  again. Touch and keyboard operable; Escape is Stop. Every state change
  disables the controls for 400 ms so a tremor double-tap cannot hit the
  button that just swapped into the same footprint.
- Truthful, *present* states: idle / preparing / listening / thinking /
  speaking / stopped, carried by the 3D Reachy Mini presence scene (or
  the breathing orb wherever WebGL is unavailable) + banner + soft
  earcons — never a silent dead wait. The scene is driven by the
  semantic expression state (static/converse/expression.js) fed only by
  real signals; the orb + full text experience remain the complete
  fallback. When realtime is configured, Live conversation is the
  primary control and Start/Done is the labeled push-to-talk fallback
  (first human-tester finding, 2026-08-31). Answers stream sentence-by-sentence
  (the ndjson turn endpoint) so speech starts after the first sentence;
  if nothing has arrived within ~1.2 s Parker says a short truthful cue
  ("Let me check.") instead of leaving dead air.
- Audio is captured only between Start and Done, encoded to 16 kHz WAV in
  the browser, sent once, never stored client-side.
- Speech out is browser speechSynthesis so Stop is immediate
  (speechSynthesis.cancel()); the microphone is never open while Parker
  speaks, so it cannot hear itself.
- Sources show as label + freshness chips. URLs are never spoken and only
  appear on hover / in the collapsed family details panel.
- A stale response (client generation bumped by Stop) is dropped, never
  rendered, never spoken.
- Typing is offered as a real fallback ("Type instead") because some days
  speech is harder — same turns, same pipeline.
"""

CONVERSE_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parker — conversation</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    font-family: -apple-system, system-ui, "Segoe UI", sans-serif;
    background: #05080d;
    color: #f4f7fb;
    display: flex;
    flex-direction: column;
    max-width: 1100px;
    margin-inline: auto;
    padding: 3vh 5vw 2vh;
    overflow-x: hidden;
  }
  main { flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 2vh; }
  [hidden] { display: none !important; }

  .label {
    font-size: clamp(.95rem, 1.6vw, 1.3rem);
    letter-spacing: .16em;
    text-transform: uppercase;
    color: #7d8ca1;
  }
  /* The Reachy Mini presence scene. Presentation only: every essential
     meaning also lives in the status text and controls. When WebGL is
     unavailable the mount stays empty and hidden, and the orb carries
     presence exactly as before. */
  #presence {
    flex: none;
    height: clamp(160px, 30vh, 320px);
    display: none;
    justify-content: center;
  }
  body.scene-active #presence { display: flex; }
  #reachy-mount {
    width: min(100%, 540px);
    height: 100%;
    background: radial-gradient(ellipse 60% 55% at 50% 62%, #0d1622 0%, rgba(5,8,13,0) 70%);
  }
  #reachy-mount canvas { width: 100% !important; height: 100% !important; display: block; }
  body.scene-active #orb { display: none; }
  @media (max-height: 640px) { #presence { height: clamp(120px, 24vh, 200px); } }
  #status-banner {
    font-size: clamp(1.6rem, 3.2vw, 2.6rem);
    font-weight: 650;
    line-height: 1.25;
    display: flex;
    align-items: center;
    gap: .7em;
    min-height: 2.6em;
  }
  /* The presence orb: one large breathing shape that carries the state the
     way a face would — color + rhythm, readable from across a room. */
  #orb {
    width: clamp(2.6rem, 6vh, 4rem);
    height: clamp(2.6rem, 6vh, 4rem);
    border-radius: 50%;
    background: #55647a;
    flex: none;
    transition: background .25s ease, transform .12s ease, box-shadow .25s ease;
  }
  body[data-state="preparing"] #orb { background: #ffd166; animation: breathe 1s ease-in-out infinite; }
  body[data-state="listening"] #orb { background: #7fe3a1; box-shadow: 0 0 40px 4px rgba(127,227,161,.35); animation: breathe 1.7s ease-in-out infinite; }
  body[data-state="thinking"]  #orb { background: #ffd166; box-shadow: 0 0 30px 2px rgba(255,209,102,.3); animation: breathe .9s ease-in-out infinite; }
  body[data-state="speaking"]  #orb { background: #6db3ff; box-shadow: 0 0 40px 4px rgba(109,179,255,.35); animation: breathe 1.4s ease-in-out infinite; }
  body[data-state="stopped"]   #orb { background: #ff9aa4; }
  #orb.pulse { transform: scale(1.22); }
  @keyframes breathe { 0%, 100% { opacity: .45; transform: scale(.96); } 50% { opacity: 1; transform: scale(1.04); } }
  @media (prefers-reduced-motion: reduce) {
    #orb, #orb.pulse { animation: none !important; transform: none !important; }
  }

  #heard-block { min-height: 2em; }
  #heard {
    font-size: clamp(1.4rem, 2.8vw, 2.2rem);
    color: #b9c6d8;
    font-style: italic;
    line-height: 1.3;
  }
  #speech {
    font-size: clamp(1.9rem, 4vw, 3.2rem);
    font-weight: 650;
    line-height: 1.25;
  }
  #sources { display: flex; flex-wrap: wrap; gap: .6rem; }
  .source-chip {
    font-size: clamp(.95rem, 1.7vw, 1.25rem);
    color: #9fd8ff;
    background: #0c1b2a;
    border: 2px solid #1f3a55;
    border-radius: 999px;
    padding: .35em .95em;
  }
  .source-chip .fresh { color: #7d8ca1; }

  #choices { display: flex; flex-direction: column; gap: 1.2vh; }
  button.choice {
    display: flex; align-items: center; gap: 1rem;
    border: 3px solid #34435c; border-radius: 20px;
    background: #0c1420; color: #f4f7fb;
    padding: clamp(.7rem, 1.8vh, 1.3rem) clamp(1rem, 2.5vw, 2rem);
    font-size: clamp(1.4rem, 2.8vw, 2.2rem);
    text-align: left; cursor: pointer; width: 100%;
    font-family: inherit;
  }
  button.choice:focus-visible, .big:focus-visible { outline: 4px solid #ffd166; outline-offset: 3px; }
  button.choice .num {
    font-size: clamp(1.8rem, 3.4vw, 2.8rem); font-weight: 800;
    background: #ffd166; color: #05080d; border-radius: 14px;
    min-width: 1.8em; text-align: center; padding: .05em .2em; flex: none;
  }
  #yes-no { display: flex; gap: 1.2rem; flex-wrap: wrap; }
  #yes-no .big { flex: 1; min-width: 10rem; }

  #controls { display: flex; gap: 1.2rem; flex-wrap: wrap; padding-top: 2vh; }
  .big {
    flex: 1;
    min-width: 12rem;
    min-height: clamp(4.2rem, 9vh, 6rem);
    font-size: clamp(1.5rem, 3vw, 2.3rem);
    font-weight: 750;
    border-radius: 22px;
    border: 3px solid transparent;
    cursor: pointer;
    font-family: inherit;
  }
  .big:disabled { opacity: .75; }
  #btn-start { background: #133c1f; color: #7fe3a1; border-color: #2e6b2e; }
  #btn-live  { background: #0c2a1c; color: #7fe3a1; border-color: #2e6b46; flex: 2; }
  /* With Live available, Start/Done is the labeled push-to-talk fallback. */
  body.live-primary #btn-start {
    background: #1a2432; color: #b9c6d8; border-color: #34435c; flex: 1;
  }
  body[data-state="live"] #orb { background: #6db3ff; box-shadow: 0 0 44px 6px rgba(109,179,255,.4); animation: breathe 2s ease-in-out infinite; }
  #btn-done  { background: #4a3a08; color: #ffd166; border-color: #8a6d1a; }
  #btn-stop  { background: #431a1f; color: #ff9aa4; border-color: #a33; }
  #btn-again { background: #1a2432; color: #b9c6d8; border-color: #34435c; }

  #type-row { display: flex; gap: .8rem; padding-top: 1vh; }
  #type-input {
    flex: 1; font-size: clamp(1.2rem, 2.2vw, 1.7rem);
    background: #0c1420; color: #f4f7fb;
    border: 3px solid #34435c; border-radius: 14px; padding: .6em .8em;
    font-family: inherit;
  }
  #type-send {
    font-size: clamp(1.2rem, 2.2vw, 1.7rem); font-weight: 700;
    background: #1a2432; color: #f4f7fb; border: 3px solid #34435c;
    border-radius: 14px; padding: .6em 1.2em; cursor: pointer; font-family: inherit;
  }

  footer {
    display: flex; justify-content: space-between; align-items: center; gap: 1rem;
    color: #8fa0b5; font-size: clamp(1rem, 1.8vw, 1.25rem); padding-top: 1.5vh;
    flex-wrap: wrap;
  }
  footer a#type-toggle {
    display: inline-block; color: #b9c6d8; text-decoration: none;
    border: 2px solid #34435c; border-radius: 999px;
    padding: .55em 1.2em; font-weight: 600;
    font-size: clamp(1rem, 1.8vw, 1.25rem);
  }
  footer a#type-toggle:focus-visible { outline: 4px solid #ffd166; outline-offset: 3px; }
  details#dev { color: #7d8ca1; font-size: .95rem; max-width: 100%; }
  details#dev pre { white-space: pre-wrap; word-break: break-word; overflow-x: auto; }
  #notice { color: #ffd166; font-size: clamp(1.05rem, 2vw, 1.4rem); min-height: 1.4em; }
</style>
</head>
<body data-state="starting">
<main>
  <div id="presence"><div id="reachy-mount"></div></div>
  <div id="status-banner"><span id="orb"></span><span id="status-text" aria-live="polite">Getting Parker ready…</span></div>
  <div id="notice" aria-live="polite"></div>
  <div id="heard-block" hidden>
    <div class="label">Parker heard</div>
    <div id="heard"></div>
  </div>
  <div id="answer-block" hidden>
    <div class="label">Parker</div>
    <div id="speech"></div>
  </div>
  <div id="sources" hidden></div>
  <div id="choices" hidden></div>
  <div id="yes-no" hidden>
    <button class="big" id="btn-yes">Yes</button>
    <button class="big" id="btn-no">No</button>
  </div>
</main>

<div id="controls">
  <button class="big" id="btn-live" hidden>Live conversation</button>
  <button class="big" id="btn-start">Start listening</button>
  <button class="big" id="btn-done" hidden>Done talking</button>
  <button class="big" id="btn-stop" hidden>Stop Parker</button>
  <button class="big" id="btn-again" hidden>Try again</button>
</div>
<form id="type-row" hidden>
  <label for="type-input" class="label" hidden>Type your question</label>
  <input id="type-input" type="text" autocomplete="off"
         placeholder="Type your question instead…" maxlength="500">
  <button id="type-send" type="submit">Send</button>
</form>

<footer>
  <span>Take your time — pauses never cut you off. Only Done sends it.</span>
  <span><a href="#" id="type-toggle">Type instead</a></span>
</footer>
<details id="dev">
  <summary>Details for the family</summary>
  <pre id="dev-out">No turns yet.</pre>
  <p><a href="/parker/sessions/ui">Review finished live sessions</a> — what Parker heard, said, injected, and staged, with latencies.</p>
</details>

<script src="/parker/converse/static/converse/expression.js"></script>
<script>
'use strict';

// ---------------------------------------------------------------------------
// State machine: starting -> idle -> preparing -> listening -> thinking ->
// speaking -> idle, with stopped reachable from anywhere. clientGen guards
// against stale results: Stop bumps it, and anything finishing under an old
// generation is dropped, never rendered, never spoken.
//
// Presence rides beside it: the semantic expression controller
// (ParkerExpression) receives the SAME real signals and drives the Reachy
// scene + the live-lane status label. It never invents state — the
// Start/Done lane forwards its own control states, the live lane forwards
// its websocket/audio events, and mic/output energy comes from the actual
// audio graph.
// ---------------------------------------------------------------------------

const expr = window.ParkerExpression ? ParkerExpression.createController() : null;

function presence(name, data) {
  if (expr) { try { expr.handleEvent(name, data); } catch (err) {} }
}

function presenceEnergy(levels) {
  if (expr) { try { expr.setEnergy(levels); } catch (err) {} }
}

// The Start/Done lane's control machine already runs on real signals; map
// its states onto the semantic phases so the two can never disagree. The
// live lane wires its own richer events and is skipped here.
const TURNS_PRESENCE = {
  idle: 'ready',
  preparing: 'connect',
  listening: 'connected',
  thinking: 'user_transcript',
  speaking: 'assistant_audio',
  stopped: 'stopped',
  error: 'error',
};

let sessionId = null;
let clientGen = 0;
let turnCounter = 0;
let abortCtl = null;
let capture = null;
let startingCapture = false;
let lastTimings = null;
let pendingAwaiting = '';
let cueTimer = null;
let realtimeAvailable = false;

const $ = (id) => document.getElementById(id);
const statusText = $('status-text');
const notice = $('notice');

const STATE_TEXT = {
  starting: 'Getting Parker ready…',
  idle: 'Tap Start listening, then ask in your own way.',
  preparing: 'Getting the microphone ready…',
  listening: 'Listening — take all the time you need. Tap Done talking when you\\u2019ve finished.',
  thinking: 'Thinking…',
  speaking: 'Parker is talking. Stop any time.',
  stopped: 'Stopped. Nothing else will happen until you start again.',
  error: 'Parker hit a snag on this laptop. Tap Start listening to try again.',
  live: 'Live — just talk, and talk over Parker any time. Stop ends it.',
};

// Controls swap identity in the same screen footprint on state change; a
// tremor double-tap must never hit the button that just appeared there.
const TAP_GUARD_MS = 400;
function guardButtons() {
  const buttons = ['btn-start', 'btn-done', 'btn-stop', 'btn-again', 'btn-yes', 'btn-no'];
  for (const id of buttons) $(id).disabled = true;
  setTimeout(() => { for (const id of buttons) $(id).disabled = false; }, TAP_GUARD_MS);
}

function setState(state, text) {
  const previous = document.body.dataset.state;
  document.body.dataset.state = state;
  statusText.textContent = text || STATE_TEXT[state] || '';
  const resting = state === 'idle' || state === 'stopped' || state === 'error';
  $('btn-start').hidden = !resting;
  $('btn-live').hidden = !(resting && realtimeAvailable);
  $('btn-done').hidden = state !== 'listening';
  $('btn-stop').hidden = !(state === 'preparing' || state === 'listening' || state === 'thinking' || state === 'speaking' || state === 'live');
  $('btn-again').hidden = !(state === 'stopped' || state === 'error');
  if (previous !== state) guardButtons();
  // Presence for the Start/Done lane; the live lane forwards its own events.
  if (previous !== state && state !== 'live' && !liveActive() && !startingLive) {
    const event = TURNS_PRESENCE[state];
    if (event === 'connect') presence('connect', {mode: 'turns'});
    else if (event) presence(event);
  }
}

function setNotice(text) { notice.textContent = text || ''; }

// ---------------------------------------------------------------------------
// Earcons: tiny synthesized cues so a tap is confirmed by ear, not just eye.
// ---------------------------------------------------------------------------

let earcuCtx = null;

function earcon(kind) {
  try {
    earcuCtx = earcuCtx || new (window.AudioContext || window.webkitAudioContext)();
    const now = earcuCtx.currentTime;
    const gain = earcuCtx.createGain();
    gain.gain.value = 0.05;
    gain.connect(earcuCtx.destination);
    const tones = kind === 'listen' ? [523, 784] : kind === 'done' ? [784] : [220];
    tones.forEach((freq, i) => {
      const osc = earcuCtx.createOscillator();
      osc.frequency.value = freq;
      osc.type = 'sine';
      osc.connect(gain);
      osc.start(now + i * 0.09);
      osc.stop(now + i * 0.09 + 0.08);
    });
  } catch (err) { /* sound is a courtesy, never a requirement */ }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderHeard(heard) {
  $('heard-block').hidden = !heard;
  $('heard').textContent = heard ? '\\u201C' + heard + '\\u201D' : '';
}

function appendSpeechText(text) {
  const block = $('answer-block');
  block.hidden = false;
  const current = $('speech').textContent;
  $('speech').textContent = current ? current + ' ' + text : text;
}

function renderSources(list) {
  const sources = $('sources');
  sources.textContent = '';
  for (const source of list || []) {
    const chip = document.createElement('span');
    chip.className = 'source-chip';
    chip.textContent = source.label;
    if (source.fresh_as_of) {
      const freshSpan = document.createElement('span');
      freshSpan.className = 'fresh';
      freshSpan.textContent = ' \\u00B7 ' + source.fresh_as_of;
      chip.appendChild(freshSpan);
    }
    if (source.url) chip.title = source.url; // hover only — never spoken
    sources.appendChild(chip);
  }
  sources.hidden = !(list || []).length;
}

function renderResult(data) {
  renderHeard(data.heard);
  if (data.speech) {
    $('answer-block').hidden = false;
    $('speech').textContent = data.speech;
  }

  renderSources(data.sources);

  const wrap = $('choices');
  wrap.textContent = '';
  pendingAwaiting = data.awaiting || '';
  const showChoices = pendingAwaiting === 'choices' && (data.choices || []).length;
  if (showChoices) {
    for (const choice of data.choices) {
      const btn = document.createElement('button');
      btn.className = 'choice';
      const num = document.createElement('span');
      num.className = 'num';
      num.textContent = choice.position;
      const text = document.createElement('span');
      text.textContent = choice.label;
      btn.appendChild(num);
      btn.appendChild(text);
      btn.addEventListener('click', () => sendText(String(choice.position)));
      wrap.appendChild(btn);
    }
  }
  wrap.hidden = !showChoices;
  $('yes-no').hidden = pendingAwaiting !== 'yes_no';

  lastTimings = data.timings_ms || null;
  renderDev(data);
}

function renderDev(data) {
  const rows = ['session ' + (sessionId || '—'), 'turn ' + (data.turn_id ?? '—') + '  kind=' + (data.kind || '—')];
  if (data.timings_ms) {
    for (const [key, value] of Object.entries(data.timings_ms)) rows.push('  ' + key + ': ' + value + ' ms');
  }
  for (const source of data.sources || []) rows.push('  source: ' + source.label + ' ' + (source.url || ''));
  $('dev-out').textContent = rows.join('\\n');
}

function clearResult() {
  for (const id of ['heard-block', 'answer-block', 'sources', 'choices', 'yes-no']) $(id).hidden = true;
  $('speech').textContent = '';
  $('heard').textContent = '';
  setNotice('');
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

async function createSession() {
  try {
    const res = await fetch('/parker/converse/sessions', {method: 'POST'});
    if (!res.ok) throw new Error('session create failed: ' + res.status);
    const data = await res.json();
    sessionId = data.session_id;
    realtimeAvailable = !!data.realtime_available;
    if (realtimeAvailable) {
      // Live is the lane you meet (first human-tester finding, 2026-08-31):
      // the flagship experience leads, Start/Done stays as the clearly
      // available push-button fallback.
      document.body.classList.add('live-primary');
      STATE_TEXT.idle = 'Tap Live conversation, then just talk.';
      const hint = document.querySelector('footer span');
      if (hint) hint.textContent =
        'Live conversation is easiest \\u2014 just talk. Start listening is the push-button way.';
    }
    if (!data.asr_ready) {
      setNotice('Voice recognition is not ready on this laptop — typing still works.');
      showTypeRow(true);
    }
    setState('idle');
  } catch (err) {
    setState('error', 'Parker\\u2019s engine is not reachable. Is the server running?');
  }
}

function postReceipt(marks) {
  if (!sessionId) return;
  const body = JSON.stringify(marks);
  try {
    navigator.sendBeacon(
      '/parker/converse/sessions/' + sessionId + '/receipts',
      new Blob([body], {type: 'application/json'})
    ) || fetch('/parker/converse/sessions/' + sessionId + '/receipts',
               {method: 'POST', headers: {'content-type': 'application/json'}, body});
  } catch (err) { /* receipts are best-effort */ }
}

// ---------------------------------------------------------------------------
// Capture: WebAudio between Start and Done, downsampled to 16 kHz WAV.
// ---------------------------------------------------------------------------

const TARGET_RATE = 16000;
const MAX_CAPTURE_SECONDS = 180;

// RMS of a float PCM block, scaled to a 0..1 presence energy. Real level
// in, nothing invented: quiet rooms sit near 0, speech lands well above
// the expression controller's hearing threshold.
function micEnergy(data) {
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
  return Math.min(1, Math.sqrt(sum / data.length) * 6);
}

async function startListening() {
  if (startingCapture || capture) return; // one microphone, one opening at a time
  startingCapture = true;
  const tapped = performance.now();
  window.speechSynthesis && speechSynthesis.cancel(); // tapping Start barges in
  clearResult();
  setState('preparing');
  if (!sessionId) { await createSession(); if (!sessionId) { startingCapture = false; return; } setState('preparing'); }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true},
    });
  } catch (err) {
    startingCapture = false;
    setNotice('Parker can\\u2019t use the microphone (permission needed). You can type instead.');
    showTypeRow(true);
    setState('idle');
    return;
  }
  if (!startingCapture) { // Stop was tapped while the mic was opening
    try { stream.getTracks().forEach((track) => track.stop()); } catch (err) {}
    return;
  }
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const gain = ctx.createGain();
  gain.gain.value = 0; // keep the graph alive without feeding the speakers
  const chunks = [];
  let samples = 0;
  proc.onaudioprocess = (event) => {
    if (!capture) return;
    const data = event.inputBuffer.getChannelData(0);
    chunks.push(new Float32Array(data));
    samples += data.length;
    presenceEnergy({user: micEnergy(data)}); // real mic level -> hearing
    if (samples / ctx.sampleRate >= MAX_CAPTURE_SECONDS) {
      setNotice('That was a long one, so I sent what I heard so far.');
      doneTalking();
    }
  };
  source.connect(proc);
  proc.connect(gain);
  gain.connect(ctx.destination);
  capture = {ctx, stream, proc, gain, chunks, rate: ctx.sampleRate, startedAt: performance.now()};
  startingCapture = false;
  earcon('listen');
  setState('listening');
  postReceipt({start_to_listening_ms: Math.round(performance.now() - tapped)});
}

function teardownCapture() {
  if (!capture) return null;
  const held = capture;
  capture = null;
  try { held.proc.disconnect(); held.gain.disconnect(); } catch (err) {}
  try { held.stream.getTracks().forEach((track) => track.stop()); } catch (err) {}
  try { held.ctx.close(); } catch (err) {}
  return held;
}

function mergeAndEncode(held) {
  let total = 0;
  for (const chunk of held.chunks) total += chunk.length;
  const all = new Float32Array(total);
  let offset = 0;
  for (const chunk of held.chunks) { all.set(chunk, offset); offset += chunk.length; }
  return encodeWav(all, held.rate, TARGET_RATE);
}

function resamplePCM16(float32, inRate, outRate) {
  const ratio = inRate / outRate;
  const outLen = Math.max(1, Math.floor(float32.length / ratio));
  const pcm = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const lo = Math.floor(pos);
    const hi = Math.min(lo + 1, float32.length - 1);
    const frac = pos - lo;
    let sample = (float32[lo] || 0) * (1 - frac) + (float32[hi] || 0) * frac;
    sample = Math.max(-1, Math.min(1, sample));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
  }
  return pcm;
}

function encodeWav(float32, inRate, outRate) {
  const pcm = resamplePCM16(float32, inRate, outRate);
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const writeString = (at, text) => { for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i)); };
  writeString(0, 'RIFF'); view.setUint32(4, 36 + pcm.length * 2, true);
  writeString(8, 'WAVE'); writeString(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, outRate, true);
  view.setUint32(28, outRate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); writeString(36, 'data');
  view.setUint32(40, pcm.length * 2, true);
  new Int16Array(buffer, 44).set(pcm);
  return buffer;
}

function bufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const STEP = 0x8000;
  for (let i = 0; i < bytes.length; i += STEP) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + STEP));
  }
  return btoa(binary);
}

async function doneTalking() {
  const held = teardownCapture();
  if (!held) return;
  earcon('done');
  const captureSeconds = (performance.now() - held.startedAt) / 1000;
  setState('thinking');
  const wav = mergeAndEncode(held);
  await sendTurn(
    {audio_base64: bufferToBase64(wav), audio_mime: 'audio/wav'},
    {capture_seconds: Math.round(captureSeconds * 10) / 10}
  );
}

// ---------------------------------------------------------------------------
// Speaking: per-sentence TTS queue over speechSynthesis
// ---------------------------------------------------------------------------

const tts = {gen: -1, outstanding: 0, started: false, finished: false, receipt: null, doneAt: 0};

function beginSpeechTurn(gen, doneAt, receipt) {
  tts.gen = gen;
  tts.outstanding = 0;
  tts.started = false;
  tts.finished = false;
  tts.receipt = receipt;
  tts.doneAt = doneAt;
}

function finishSpeechTurn() {
  if (tts.finished) return;
  tts.finished = true;
  if (tts.gen === clientGen) {
    if (pendingAwaiting === 'choices') setState('idle', 'Tap a choice \\u2014 or Start listening and say the number.');
    else if (pendingAwaiting === 'yes_no') setState('idle', 'Tap Yes or No \\u2014 or say it out loud.');
    else setState('idle', 'Ask a follow-up any time \\u2014 tap Start listening.');
  } else if (tts.receipt) {
    tts.receipt.outcome = 'stopped';
  }
  if (tts.receipt) postReceipt(tts.receipt);
  tts.receipt = null;
}

function speakText(text) {
  if (!text || !window.speechSynthesis) return false;
  const gen = tts.gen;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95;
  tts.outstanding += 1;
  utterance.onstart = () => {
    if (gen !== clientGen) { speechSynthesis.cancel(); return; }
    if (!tts.started) {
      tts.started = true;
      if (tts.receipt) {
        tts.receipt.response_to_first_audio_ms = Math.round(performance.now() - tts.doneAt - (tts.receipt.done_to_response_ms || 0));
        tts.receipt.done_to_first_audio_ms = Math.round(performance.now() - tts.doneAt);
      }
    }
    setState('speaking');
  };
  utterance.onboundary = () => {
    const orb = $('orb');
    orb.classList.add('pulse');
    setTimeout(() => orb.classList.remove('pulse'), 90);
    // Word boundaries are the real output signal this lane has.
    presenceEnergy({parker: 0.85});
    setTimeout(() => presenceEnergy({parker: 0.2}), 160);
  };
  const settle = () => {
    tts.outstanding -= 1;
    presenceEnergy({parker: 0});
    if (tts.outstanding <= 0 && tts.turnComplete) finishSpeechTurn();
  };
  utterance.onend = settle;
  utterance.onerror = settle;
  speechSynthesis.speak(utterance);
  return true;
}

function scheduleThinkingCue(gen) {
  clearTimeout(cueTimer);
  cueTimer = setTimeout(() => {
    if (gen !== clientGen) return;
    if (document.body.dataset.state !== 'thinking') return;
    // A short truthful cue instead of dead air — never a fake answer.
    speakText('Let me check.');
  }, 1200);
}

// ---------------------------------------------------------------------------
// Turns: the streaming endpoint — speak sentence one while the rest arrives
// ---------------------------------------------------------------------------

const TURN_TIMEOUT_MS = 60000;

async function postTurnStream(body, myTurn) {
  abortCtl = new AbortController();
  const watchdog = setTimeout(() => { try { abortCtl.abort(); } catch (err) {} }, TURN_TIMEOUT_MS);
  try {
    return await fetch('/parker/converse/sessions/' + sessionId + '/turns/stream', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(Object.assign({turn_id: myTurn}, body)),
      signal: abortCtl.signal,
    });
  } finally {
    clearTimeout(watchdog);
  }
}

async function sendTurn(body, marks) {
  if (!sessionId) { await createSession(); if (!sessionId) return; }
  const myGen = clientGen;
  const myTurn = ++turnCounter;
  const doneAt = performance.now();
  setState('thinking');
  scheduleThinkingCue(myGen);

  const receipt = Object.assign({turn_id: myTurn}, marks || {});
  beginSpeechTurn(myGen, doneAt, receipt);
  tts.turnComplete = false;
  let finalEvent = null;
  let errorEvent = null;
  let streamed = false;
  let retried = false;
  const streamedParts = [];

  const handleEvent = (event) => {
    if (myGen !== clientGen) return;
    if (event.event === 'heard') {
      renderHeard(event.heard);
    } else if (event.event === 'speech') {
      clearTimeout(cueTimer);
      streamed = true;
      streamedParts.push(event.text);
      appendSpeechText(event.text);
      speakText(event.text);
    } else if (event.event === 'final') {
      finalEvent = event;
    } else if (event.event === 'error') {
      errorEvent = event;
    }
  };

  const readStream = async () => {
    const res = await postTurnStream(body, myTurn);
    if (!res.ok) throw new Error('turn failed: ' + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, {stream: true});
      let newline;
      while ((newline = buffered.indexOf('\\n')) >= 0) {
        const line = buffered.slice(0, newline);
        buffered = buffered.slice(newline + 1);
        if (line.trim()) handleEvent(JSON.parse(line));
      }
    }
  };

  try {
    await readStream();
    if (errorEvent && errorEvent.status === 404 && !retried && myGen === clientGen) {
      // The session idled out or was evicted; recover invisibly, once.
      retried = true;
      errorEvent = null;
      sessionId = null;
      await createSession();
      if (sessionId && myGen === clientGen) await readStream();
    }
  } catch (err) {
    clearTimeout(cueTimer);
    if (myGen !== clientGen) return; // stopped: silence is the right outcome
    if ((err && err.name) === 'AbortError') {
      setNotice('That took too long, so I let it go. Please try again.');
    } else {
      setNotice('Parker couldn\\u2019t answer that one. Please try again.');
    }
    setState('idle');
    return;
  }
  clearTimeout(cueTimer);
  if (myGen !== clientGen) return; // stale — drop everything

  if (errorEvent) {
    setNotice('Parker couldn\\u2019t answer that one. Please try again.');
    setState('idle');
    return;
  }
  if (!finalEvent || finalEvent.state === 'stopped') return;

  receipt.outcome = finalEvent.kind || finalEvent.state;
  receipt.done_to_response_ms = Math.round(performance.now() - doneAt);
  renderResult(finalEvent);
  const finalSpeech = finalEvent.speech || '';
  const spokenSoFar = streamedParts.join(' ');
  if (!streamed) {
    tts.turnComplete = true;
    if (!speakText(finalSpeech)) finishSpeechTurn();
  } else if (finalEvent.kind === 'refused' || !finalSpeech.startsWith(spokenSoFar)) {
    // The final reply DIVERGED from what streamed (guard redirect, an
    // honest brain-failure line, a re-trimmed answer): silence the stream
    // and speak the authoritative version instead.
    speechSynthesis.cancel();
    beginSpeechTurn(myGen, doneAt, receipt);
    tts.turnComplete = true;
    if (!speakText(finalSpeech)) finishSpeechTurn();
  } else {
    // What streamed is a strict prefix: speak only the remainder — this is
    // where the confirmation question and 'Want more detail?' live, and a
    // question Parker never asks aloud is a broken loop for a voice-first
    // user (found by the adversarial verifier).
    const remainder = finalSpeech.slice(spokenSoFar.length).trim();
    tts.turnComplete = true;
    if (remainder) {
      speakText(remainder);
    } else if (tts.outstanding <= 0) {
      finishSpeechTurn();
    }
  }
}

function sendText(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return;
  window.speechSynthesis && speechSynthesis.cancel();
  clearResult();
  sendTurn({text: trimmed}, {});
}

// ---------------------------------------------------------------------------
// Live conversation: full-duplex over the server relay (gpt-realtime).
// Parker's server stays the policy boundary; this side is mic in, audio out,
// live captions, and one big way out.
// ---------------------------------------------------------------------------

const LIVE_RATE = 24000;
const live = {ws: null, micCtx: null, micStream: null, proc: null, gain: null,
              playCtx: null, nextTime: 0, sources: [], chunkMeta: [],
              energyTimer: null, wasPlaying: false};
let startingLive = false;

// Output-energy truth for the live lane: each scheduled chunk knows its
// window and loudness; a light timer reads whichever chunk is actually
// playing NOW and reports drain once the queue really empties.
function watchLivePlayback() {
  if (live.energyTimer) return;
  live.energyTimer = setInterval(() => {
    if (!live.playCtx) return;
    const now = live.playCtx.currentTime;
    live.chunkMeta = live.chunkMeta.filter((c) => c.at + c.dur > now);
    const current = live.chunkMeta.find((c) => c.at <= now);
    if (current) {
      live.wasPlaying = true;
      presenceEnergy({parker: current.energy});
    } else {
      presenceEnergy({parker: 0});
      if (live.wasPlaying && now >= live.nextTime) {
        live.wasPlaying = false;
        presence('assistant_audio_drained');
      }
    }
  }, 120);
}

function liveActive() { return !!live.ws; }

async function startLive() {
  if (liveActive() || startingLive || capture || startingCapture) return;
  startingLive = true; // one live line, one opening at a time
  window.speechSynthesis && speechSynthesis.cancel();
  clearResult();
  presence('connect', {mode: 'live'});
  setState('preparing');
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true},
    });
  } catch (err) {
    startingLive = false;
    setNotice('Parker can\\u2019t use the microphone (permission needed).');
    setState('idle');
    return;
  }
  if (!startingLive) { // Stop was tapped while the mic was opening
    try { stream.getTracks().forEach((track) => track.stop()); } catch (err) {}
    return;
  }
  const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
  const ws = new WebSocket(scheme + location.host + '/parker/converse/realtime');
  live.ws = ws;
  live.micStream = stream;
  live.playCtx = new (window.AudioContext || window.webkitAudioContext)();
  live.micCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = live.micCtx.createMediaStreamSource(stream);
  live.proc = live.micCtx.createScriptProcessor(4096, 1, 1);
  live.gain = live.micCtx.createGain();
  live.gain.gain.value = 0;
  live.proc.onaudioprocess = (event) => {
    if (!live.ws || live.ws.readyState !== 1) return;
    const data = event.inputBuffer.getChannelData(0);
    presenceEnergy({user: micEnergy(data)}); // real mic level -> hearing
    const pcm = resamplePCM16(data, live.micCtx.sampleRate, LIVE_RATE);
    live.ws.send(JSON.stringify({type: 'audio', data: bufferToBase64(pcm.buffer)}));
  };
  source.connect(live.proc);
  live.proc.connect(live.gain);
  live.gain.connect(live.micCtx.destination);
  watchLivePlayback();

  startingLive = false;
  ws.onopen = () => { earcon('listen'); presence('connected'); setState('live'); };
  ws.onmessage = (message) => {
    if (ws !== live.ws) return; // a frame from a dead line must change nothing
    let event;
    try { event = JSON.parse(message.data); } catch (err) { return; }
    if (!event || typeof event !== 'object') return;
    handleLiveEvent(event);
  };
  ws.onclose = () => { if (ws === live.ws) endLive('The live line closed.'); };
  ws.onerror = () => { if (ws === live.ws) endLive('The live line dropped.'); };
}

function handleLiveEvent(event) {
  if (event.type === 'audio') {
    playLivePcm(event.data);
  } else if (event.type === 'user_transcript') {
    presence('user_transcript');
    renderHeard(event.text);
    $('speech').textContent = '';
    $('answer-block').hidden = true;
    $('sources').hidden = true; // last turn's evidence must not linger
  } else if (event.type === 'sources') {
    renderSources(event.items);
  } else if (event.type === 'working') {
    // Real background-work presence from the bridge: started / done / failed.
    const status = event.status === 'started' ? 'work_start'
      : event.status === 'failed' ? 'work_failed' : 'work_done';
    presence(status, {kind: event.kind || 'search'});
  } else if (event.type === 'closing') {
    // Parker said goodbye; let the scheduled audio finish before hanging up.
    presence('closing');
    const remaining = live.playCtx
      ? Math.max(0, (live.nextTime - live.playCtx.currentTime) * 1000)
      : 0;
    setTimeout(() => { if (liveActive()) endLive('The call wrapped up.'); }, remaining + 300);
  } else if (event.type === 'assistant_transcript_delta') {
    appendSpeechText(event.text);
  } else if (event.type === 'clear') {
    // The scene yields only when playback was actually flushed — the
    // server sends `clear` on every speech_started, talking or not.
    if (flushLivePlayback()) presence('interrupted');
  } else if (event.type === 'guard_redirect') {
    presence('guard_redirect');
    flushLivePlayback();
    $('speech').textContent = event.text;
    $('answer-block').hidden = false;
    // The model's audio was cancelled server-side; the redirect must be
    // HEARD, not just shown — speakNow bypasses the turn-generation queue
    // (found by the adversarial verifier: speakText's stale-gen check
    // silently cancelled it on a fresh page).
    speakNow(event.text);
  } else if (event.type === 'proposal_staged') {
    presence('proposal_staged');
    setNotice('On the screen to confirm: ' + (event.label || 'a suggested action'));
  } else if (event.type === 'notice' || event.type === 'unavailable') {
    setNotice(event.text || '');
    if (event.type === 'unavailable') { endLive(''); presence('offline'); }
  }
}

function playLivePcm(encoded) {
  try {
    if (!live.playCtx) return;
    const raw = atob(encoded);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const usable = bytes.length - (bytes.length % 2);
    if (!usable) return;
    const pcm = new Int16Array(bytes.buffer, 0, usable / 2);
    const floats = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) floats[i] = pcm[i] / 32768;
    const buffer = live.playCtx.createBuffer(1, floats.length, LIVE_RATE);
    buffer.getChannelData(0).set(floats);
    const src = live.playCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(live.playCtx.destination);
    const at = Math.max(live.playCtx.currentTime + 0.05, live.nextTime);
    src.start(at);
    live.nextTime = at + buffer.duration;
    live.sources.push(src);
    src.onended = () => { live.sources = live.sources.filter((s) => s !== src); };
    // Presence: audio is genuinely scheduled — talking, with the chunk's
    // own loudness carried for audio-reactive motion.
    let sum = 0;
    for (let i = 0; i < floats.length; i++) sum += floats[i] * floats[i];
    live.chunkMeta.push({
      at: at, dur: buffer.duration,
      energy: Math.min(1, Math.sqrt(sum / floats.length) * 4),
    });
    presence('assistant_audio');
    const orb = $('orb');
    orb.classList.add('pulse');
    setTimeout(() => orb.classList.remove('pulse'), 90);
  } catch (err) { /* one bad chunk must not end the call */ }
}

function flushLivePlayback() {
  const hadAudio = live.sources.length > 0;
  for (const src of live.sources) { try { src.stop(); } catch (err) {} }
  live.sources = [];
  live.chunkMeta = [];
  live.nextTime = 0;
  live.wasPlaying = false;
  presenceEnergy({parker: 0});
  return hadAudio;
}

function speakNow(text) {
  // Immediate speech outside the turn lifecycle (live-lane guard redirect).
  if (!text || !window.speechSynthesis) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95;
  speechSynthesis.speak(utterance);
}

function endLive(noticeText) {
  startingLive = false;
  const ws = live.ws;
  live.ws = null;
  if (live.energyTimer) { clearInterval(live.energyTimer); live.energyTimer = null; }
  flushLivePlayback();
  if (ws) {
    try { ws.send(JSON.stringify({type: 'end'})); } catch (err) {}
    try { ws.close(); } catch (err) {}
  }
  try { live.proc && live.proc.disconnect(); live.gain && live.gain.disconnect(); } catch (err) {}
  try { live.micStream && live.micStream.getTracks().forEach((track) => track.stop()); } catch (err) {}
  try { live.micCtx && live.micCtx.close(); } catch (err) {}
  try { live.playCtx && live.playCtx.close(); } catch (err) {}
  live.micCtx = null; live.micStream = null; live.proc = null; live.gain = null; live.playCtx = null;
  if (noticeText) setNotice(noticeText);
  setState('stopped');
}

// ---------------------------------------------------------------------------
// Stop: cancel speech, abort the request, invalidate both generations.
// ---------------------------------------------------------------------------

function stopParker() {
  if (liveActive() || startingLive) {
    // In live mode there is one big way out: silence now, line closed —
    // including a microphone that is still opening.
    earcon('stop');
    endLive('');
    return;
  }
  const tapped = performance.now();
  clientGen++;
  startingCapture = false; // discard a microphone that is still opening
  clearTimeout(cueTimer);
  try { window.speechSynthesis && speechSynthesis.cancel(); } catch (err) {}
  if (abortCtl) { try { abortCtl.abort(); } catch (err) {} }
  teardownCapture();
  if (sessionId) {
    fetch('/parker/converse/sessions/' + sessionId + '/stop', {method: 'POST', keepalive: true})
      .catch(() => {});
  }
  earcon('stop');
  postReceipt({stop_to_silence_ms: Math.round(performance.now() - tapped), outcome: 'stopped'});
  // Silence the voice but keep the words: he may have stopped Parker
  // precisely because the answer on screen is already enough.
  $('choices').hidden = true;
  $('yes-no').hidden = true;
  setNotice('');
  setState('stopped');
}

function tryAgain() { clearResult(); startListening(); }

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function showTypeRow(show) { $('type-row').hidden = !show; if (show) $('type-input').focus(); }

$('btn-start').addEventListener('click', startListening);
$('btn-live').addEventListener('click', startLive);
$('btn-done').addEventListener('click', doneTalking);
$('btn-stop').addEventListener('click', stopParker);
$('btn-again').addEventListener('click', tryAgain);
$('btn-yes').addEventListener('click', () => sendText('yes'));
$('btn-no').addEventListener('click', () => sendText('no'));
$('type-toggle').addEventListener('click', (event) => { event.preventDefault(); showTypeRow($('type-row').hidden); });
// A real form: Enter in the input and the Send button both submit natively.
$('type-row').addEventListener('submit', (event) => {
  event.preventDefault();
  sendText($('type-input').value);
  $('type-input').value = '';
});
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') stopParker(); });
window.addEventListener('pagehide', () => {
  if (!sessionId) return;
  try {
    navigator.sendBeacon('/parker/converse/sessions/' + sessionId + '/end', new Blob(['{}'], {type: 'application/json'}));
  } catch (err) {}
});

// The live lane's status label reads from the semantic state, so the words
// and the pose can never disagree; the Start/Done lane keeps its longer
// coaching lines. Exposed on window for the renderer module (and for
// driving the real controller with synthetic fixtures during testing).
if (expr) {
  expr.subscribe((s) => {
    if (document.body.dataset.state === 'live') {
      statusText.textContent = ParkerExpression.describe(s);
    }
  });
}
window.ParkerPresence = {controller: expr};

createSession();
</script>
<script type="module">
// The Reachy Mini scene boots independently of the conversation script:
// a slow import or a WebGL failure never delays the microphone, and on
// failure the page simply keeps the orb + full text experience.
(async () => {
  const mount = document.getElementById('reachy-mount');
  const controller = window.ParkerPresence && window.ParkerPresence.controller;
  if (!mount || !controller) return;
  const reduced = window.matchMedia
    && matchMedia('(prefers-reduced-motion: reduce)').matches;
  try {
    const mod = await import('/parker/converse/static/converse/reachy.js');
    const scene = mod.createReachyScene(mount, controller, {reducedMotion: !!reduced});
    if (scene) {
      document.body.classList.add('scene-active');
      window.ParkerPresence.scene = scene;
    }
  } catch (err) { /* no WebGL / no module: the orb remains the presence */ }
})();
</script>
</body>
</html>
"""
