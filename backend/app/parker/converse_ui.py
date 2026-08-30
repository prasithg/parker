"""Single-file Patient Curiosity Loop page — GET /parker/converse.

The first-user surface from the 2026-08-29 strategy doc: tap Start, take
your time (pauses never cut you off — only your own Done ends the turn),
see what Parker heard, get a brief current answer with its source named on
screen, tap a choice or say a follow-up, and Stop instantly.

Design contract (pinned by tests):

- Four large controls: Start listening, Done talking, Stop Parker, Try
  again. Touch and keyboard operable; Escape is Stop.
- Truthful states: idle / listening / thinking / speaking / stopped. The
  listening indicator appears on the same tap that starts capture.
- Audio is captured only between Start and Done, encoded to 16 kHz WAV in
  the browser, sent once, and never stored client-side.
- Speech out is browser speechSynthesis so Stop is immediate
  (speechSynthesis.cancel()); the microphone is never open while Parker
  speaks, so it cannot hear itself.
- Sources show as label + freshness chips. URLs are never spoken and only
  appear inside the collapsed family details panel.
- A stale response (client generation bumped by Stop) is dropped, never
  rendered, never spoken.
- Typing is offered as a quiet fallback ("Type instead") because some
  days speech is harder — same turns, same pipeline.
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
  #status-banner {
    font-size: clamp(1.6rem, 3.2vw, 2.6rem);
    font-weight: 650;
    line-height: 1.25;
    display: flex;
    align-items: center;
    gap: .6em;
  }
  #status-dot {
    width: .55em; height: .55em; border-radius: 50%;
    background: #55647a; flex: none;
  }
  body[data-state="listening"] #status-dot { background: #7fe3a1; animation: breathe 1.6s ease-in-out infinite; }
  body[data-state="thinking"]  #status-dot { background: #ffd166; animation: breathe 1.1s ease-in-out infinite; }
  body[data-state="speaking"]  #status-dot { background: #6db3ff; animation: breathe 1.6s ease-in-out infinite; }
  body[data-state="stopped"]   #status-dot { background: #ff9aa4; }
  @keyframes breathe { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }

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
  }
  button.choice:focus-visible, .big:focus-visible { outline: 4px solid #ffd166; outline-offset: 3px; }
  button.choice .num {
    font-size: clamp(1.8rem, 3.4vw, 2.8rem); font-weight: 800;
    background: #ffd166; color: #05080d; border-radius: 14px;
    min-width: 1.8em; text-align: center; padding: .05em .2em; flex: none;
  }
  #yes-no { display: flex; gap: 1.2rem; flex-wrap: wrap; }
  #yes-no .big { flex: 1; min-width: 10rem; }

  #controls {
    display: flex; gap: 1.2rem; flex-wrap: wrap;
    padding-top: 2vh;
  }
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
  #btn-start { background: #133c1f; color: #7fe3a1; border-color: #2e6b2e; }
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
  <div id="status-banner"><span id="status-dot"></span><span id="status-text">Getting Parker ready…</span></div>
  <div id="notice"></div>
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
</details>

<script>
'use strict';

// ---------------------------------------------------------------------------
// State machine: starting -> idle -> listening -> thinking -> speaking ->
// idle (ready for the follow-up), with stopped reachable from anywhere.
// clientGen guards against stale results: Stop bumps it, and anything that
// finishes under an old generation is dropped, never rendered, never spoken.
// ---------------------------------------------------------------------------

let sessionId = null;
let clientGen = 0;
let turnCounter = 0;
let abortCtl = null;
let capture = null;          // {ctx, stream, proc, gain, chunks, samples, rate, startedAt, timer}
let lastTimings = null;
let pendingAwaiting = '';

const $ = (id) => document.getElementById(id);
const statusText = $('status-text');
const notice = $('notice');

const STATE_TEXT = {
  starting: 'Getting Parker ready…',
  idle: 'Tap Start listening, then ask in your own way.',
  preparing: 'Getting the microphone ready…',
  listening: 'Listening — take all the time you need. Tap Done talking when you\\u2019ve finished.',
  thinking: 'One moment…',
  speaking: 'Parker is answering. Stop any time.',
  stopped: 'Stopped. Nothing else will happen until you start again.',
  error: 'Parker hit a snag on this laptop. Tap Start listening to try again.',
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
  $('btn-start').hidden = !(state === 'idle' || state === 'stopped' || state === 'error');
  $('btn-done').hidden = state !== 'listening';
  $('btn-stop').hidden = !(state === 'preparing' || state === 'listening' || state === 'thinking' || state === 'speaking');
  $('btn-again').hidden = !(state === 'stopped' || state === 'error');
  if (previous !== state) guardButtons();
}

function setNotice(text) { notice.textContent = text || ''; }

function renderResult(data) {
  $('heard-block').hidden = !data.heard;
  $('heard').textContent = data.heard ? '\\u201C' + data.heard + '\\u201D' : '';
  $('answer-block').hidden = !data.speech;
  $('speech').textContent = data.speech || '';

  const sources = $('sources');
  sources.textContent = '';
  for (const source of data.sources || []) {
    const chip = document.createElement('span');
    chip.className = 'source-chip';
    const fresh = source.fresh_as_of ? ' \\u00B7 ' + source.fresh_as_of : '';
    chip.textContent = source.label;
    if (fresh) {
      const freshSpan = document.createElement('span');
      freshSpan.className = 'fresh';
      freshSpan.textContent = fresh;
      chip.appendChild(freshSpan);
    }
    if (source.url) chip.title = source.url; // hover only — never spoken
    sources.appendChild(chip);
  }
  sources.hidden = !(data.sources || []).length;

  const wrap = $('choices');
  wrap.textContent = '';
  pendingAwaiting = data.awaiting || '';
  const showChoices = pendingAwaiting === 'choices' && (data.choices || []).length;
  if (showChoices) {
    for (const choice of data.choices) {
      const btn = document.createElement('button');
      btn.className = 'choice';
      btn.dataset.position = choice.position;
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
// No MediaRecorder, no server-side decode dependency, nothing stored.
// ---------------------------------------------------------------------------

const TARGET_RATE = 16000;
const MAX_CAPTURE_SECONDS = 180;

let startingCapture = false;

async function startListening() {
  if (startingCapture || capture) return; // one microphone, one opening at a time
  startingCapture = true;
  const tapped = performance.now();
  window.speechSynthesis && speechSynthesis.cancel(); // tapping Start barges in
  clearResult();
  // Honest instant feedback: the banner changes on the tap itself, before
  // the permission/device call resolves.
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

function encodeWav(float32, inRate, outRate) {
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
  const captureSeconds = (performance.now() - held.startedAt) / 1000;
  setState('thinking');
  const wav = mergeAndEncode(held);
  await sendTurn(
    {audio_base64: bufferToBase64(wav), audio_mime: 'audio/wav'},
    {capture_seconds: Math.round(captureSeconds * 10) / 10}
  );
}

// ---------------------------------------------------------------------------
// Turns
// ---------------------------------------------------------------------------

const TURN_TIMEOUT_MS = 45000;

async function postTurn(body, myTurn) {
  abortCtl = new AbortController();
  const watchdog = setTimeout(() => { try { abortCtl.abort(); } catch (err) {} }, TURN_TIMEOUT_MS);
  try {
    const res = await fetch('/parker/converse/sessions/' + sessionId + '/turns', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(Object.assign({turn_id: myTurn}, body)),
      signal: abortCtl.signal,
    });
    return res;
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
  let data;
  try {
    let res = await postTurn(body, myTurn);
    if (res.status === 404) {
      // The session idled out or was evicted; recover invisibly, once.
      sessionId = null;
      await createSession();
      if (!sessionId || myGen !== clientGen) return;
      res = await postTurn(body, myTurn);
    }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || ('turn failed: ' + res.status));
    }
    data = await res.json();
  } catch (err) {
    if (myGen !== clientGen) return; // stopped: silence is the right outcome
    // Never put a raw developer string on this screen.
    const raw = String((err && err.message) || err);
    if ((err && err.name) === 'AbortError') {
      setNotice('That took too long, so I let it go. Please try again.');
    } else if (raw.indexOf('exceeds') !== -1 || raw.indexOf('audio') !== -1) {
      setNotice('Parker couldn\\u2019t use that recording. Please try again.');
    } else {
      setNotice('Parker couldn\\u2019t answer that one. Please try again.');
    }
    setState('idle');
    return;
  }
  if (myGen !== clientGen || data.state === 'stopped') return; // stale — drop it
  const receipt = Object.assign({turn_id: myTurn, outcome: data.kind || data.state,
    done_to_response_ms: Math.round(performance.now() - doneAt)}, marks || {});
  renderResult(data);
  if (data.state === 'silence') setNotice('');
  speak(data.speech, myGen, doneAt, receipt);
}

function sendText(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return;
  window.speechSynthesis && speechSynthesis.cancel();
  clearResult();
  sendTurn({text: trimmed}, {});
}

function speak(text, gen, doneAt, receipt) {
  let finished = false;
  let started = false;
  const finish = () => {
    if (finished) return; // onend and onerror can both fire
    finished = true;
    if (gen !== clientGen) receipt.outcome = 'stopped'; // the answer never fully landed
    if (gen === clientGen) {
      if (pendingAwaiting === 'choices') setState('idle', 'Tap a choice \\u2014 or Start listening and say the number.');
      else if (pendingAwaiting === 'yes_no') setState('idle', 'Tap Yes or No \\u2014 or say it out loud.');
      else setState('idle', 'Ask a follow-up any time \\u2014 tap Start listening.');
    }
    postReceipt(receipt);
  };
  if (!text || !window.speechSynthesis) { finish(); return; }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95;
  utterance.onstart = () => {
    if (finished) { speechSynthesis.cancel(); return; }
    if (gen !== clientGen) { speechSynthesis.cancel(); return; }
    started = true;
    receipt.response_to_first_audio_ms = Math.round(performance.now() - doneAt - (receipt.done_to_response_ms || 0));
    receipt.done_to_first_audio_ms = Math.round(performance.now() - doneAt);
    setState('speaking');
  };
  utterance.onend = finish;
  utterance.onerror = finish;
  // Some browsers fire neither onstart nor onend; the answer is on screen
  // either way, so the page must not stick at "One moment…".
  setTimeout(() => { if (!started) finish(); }, 5000);
  speechSynthesis.speak(utterance);
}

// ---------------------------------------------------------------------------
// Stop: cancel speech, abort the request, invalidate both generations.
// ---------------------------------------------------------------------------

function stopParker() {
  const tapped = performance.now();
  clientGen++;
  startingCapture = false; // discard a microphone that is still opening
  try { window.speechSynthesis && speechSynthesis.cancel(); } catch (err) {}
  if (abortCtl) { try { abortCtl.abort(); } catch (err) {} }
  teardownCapture();
  if (sessionId) {
    fetch('/parker/converse/sessions/' + sessionId + '/stop', {method: 'POST', keepalive: true})
      .catch(() => {});
  }
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

createSession();
</script>
</body>
</html>
"""
