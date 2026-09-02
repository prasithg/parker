"""The companion page — GET /parker/converse.

The virtual Reachy Mini embodiment (chairman direction 2026-09-01,
docs/plans/2026-09-01-companion-take2.md): a simulation of the robot that
will sit in the living room. The screen holds the Reachy, one power
switch, and one CC toggle — nothing else. No typing, no per-turn buttons,
no transcript panel, no numbered choices. Voice is the whole interface.

Contract (pinned by tests):

- Power is real and ENGINE-owned (docs/plans/2026-09-01-foundation-
  closure-overnight.md): the page claims power and shows ON only after
  the engine acknowledged; every wake/realtime socket presents the owner
  token + generation the claim issued; off releases microphone, sockets,
  speech, and playback here first, then persists (a failed write is
  retried and said out loud, never swallowed); the engine revokes every
  other screen's sockets on off, and a second screen cannot displace one
  that is listening. Off stays off across restarts. On = DORMANT: the mic
  feeds only the LOCAL wake lane (no cloud audio) and Reachy rests
  lifeless until "Hey Parker" pops it awake into the live full-duplex
  line; the words after the wake phrase ride the line's first frame; the
  session's gentle wind-down returns it to dormancy
  (docs/plans/2026-09-01-wake-word.md). A missing local wake model fails
  CLOSED (power off, honest card) — never continuous cloud audio. A
  dropped line retries once per activation, then rests honestly.
- CC (closed captions) is optional and persisted: TV-style captions of
  what Parker heard and what it is saying. Off by default.
- Action truth outranks the avatar: staged offers, execution outcomes,
  guard redirects, and honest line errors appear as transient cards even
  with CC off. Confirmation is SPOKEN (yes/no) — the card says so and
  never asks him to tap anything.
- The semantic expression state (static/converse/expression.js) drives
  the scene and the screen-reader status; every motion derives from real
  runtime signals. Stale sockets, late TTS callbacks, and page hide are
  fenced exactly like the lab page (independent review, 2026-09-01).
"""

COMPANION_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parker</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    font-family: -apple-system, system-ui, "Segoe UI", sans-serif;
    background: #04060a;
    color: #f4f7fb;
    overflow: hidden;
  }
  [hidden] { display: none !important; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap;
  }

  /* The Reachy fills the room. */
  #reachy-mount { position: fixed; inset: 0; }
  #reachy-mount canvas { width: 100% !important; height: 100% !important; display: block; }

  /* No-WebGL fallback: one large breathing presence dot. */
  #orb-fallback {
    position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
  }
  #orb-fallback .dot {
    width: clamp(6rem, 22vh, 12rem); height: clamp(6rem, 22vh, 12rem);
    border-radius: 50%; background: #2a3648;
    transition: background .3s ease, box-shadow .3s ease;
  }
  body[data-power="on"] #orb-fallback .dot { background: #7fe3a1; box-shadow: 0 0 80px 10px rgba(127,227,161,.3); animation: breathe 2s ease-in-out infinite; }
  body[data-power="dormant"] #orb-fallback .dot { background: #223041; animation: breathe 5s ease-in-out infinite; }
  body[data-power="starting"] #orb-fallback .dot { background: #ffd166; animation: breathe 1s ease-in-out infinite; }
  body[data-power="error"] #orb-fallback .dot { background: #ff9aa4; }
  @keyframes breathe { 0%, 100% { opacity: .5; transform: scale(.96); } 50% { opacity: 1; transform: scale(1.05); } }
  @media (prefers-reduced-motion: reduce) { #orb-fallback .dot { animation: none !important; } }

  /* Captions: TV-style, bottom third, only when CC is on. */
  #cc {
    position: fixed; left: 50%; bottom: 16vh; transform: translateX(-50%);
    max-width: min(86vw, 60rem); display: flex; flex-direction: column; gap: .5rem;
    align-items: center; pointer-events: none;
  }
  #cc .line {
    background: rgba(0,0,0,.78); border-radius: 12px;
    padding: .35em .8em; line-height: 1.3; text-align: center;
    font-size: clamp(1.3rem, 2.6vw, 2rem);
  }
  #cc-him { color: #b9c6d8; font-style: italic; }
  #cc-parker { color: #ffffff; font-weight: 600; }
  #cc-source { color: #8fb3d9; font-size: clamp(1rem, 1.8vw, 1.3rem); }

  /* Action/guard/error truth: one visible card, above the bottom bar. */
  #card, #alert {
    position: fixed; left: 50%; bottom: calc(7rem + 4vh); transform: translateX(-50%);
    max-width: min(88vw, 46rem);
    border-radius: 18px; padding: 1rem 1.4rem;
    font-size: clamp(1.25rem, 2.4vw, 1.7rem); line-height: 1.35;
    border: 2px solid #34435c; background: #0c1420; text-align: center;
  }
  #card.staged { border-color: #8a6d1a; background: #221b06; color: #ffd166; }
  #card.executed { border-color: #2e6b46; background: #0c2a1c; color: #7fe3a1; }
  #alert.failed { border-color: #a33; background: #2a1114; color: #ff9aa4; }
  #card.cancelled, #card.notice { border-color: #34435c; background: #0c1420; color: #b9c6d8; }
  #alert.guard { border-color: #8a6d1a; background: #221b06; color: #ffd166; }
  #alert.error { border-color: #a33; background: #2a1114; color: #ff9aa4; }

  /* The only controls in the room: CC and power. */
  #bottom-bar {
    position: fixed; left: 0; right: 0; bottom: 0;
    display: flex; align-items: center; justify-content: space-between;
    padding: 1.2rem clamp(1rem, 4vw, 3rem);
    gap: 1rem;
  }
  #cc-toggle {
    font-family: inherit; cursor: pointer;
    font-size: clamp(1rem, 1.8vw, 1.25rem); font-weight: 700;
    color: #7d8ca1; background: #0c1420; border: 2px solid #34435c;
    border-radius: 999px; padding: .7em 1.4em; min-height: 44px;
  }
  #cc-toggle[aria-pressed="true"] { color: #f4f7fb; border-color: #7d8ca1; }
  #power {
    font-family: inherit; cursor: pointer;
    display: flex; align-items: center; gap: .9rem;
    min-height: clamp(4rem, 9vh, 5.2rem); min-width: clamp(11rem, 24vw, 16rem);
    justify-content: center;
    font-size: clamp(1.3rem, 2.4vw, 1.8rem); font-weight: 800;
    border-radius: 999px; border: 3px solid #2e6b46;
    background: #0c2a1c; color: #7fe3a1;
  }
  #power .lamp {
    width: 1em; height: 1em; border-radius: 50%; background: #2a3648; flex: none;
    transition: background .25s ease, box-shadow .25s ease;
  }
  body[data-power="on"] #power .lamp { background: #7fe3a1; box-shadow: 0 0 18px 2px rgba(127,227,161,.6); }
  body[data-power="dormant"] #power .lamp { background: #3f6b52; animation: breathe 4s ease-in-out infinite; }
  body[data-power="starting"] #power .lamp { background: #ffd166; }
  body[data-power="error"] #power .lamp { background: #ff9aa4; }
  body[data-power="elsewhere"] #power .lamp { background: #6f7f99; }
  body[data-power="elsewhere"] #power { border-color: #34435c; background: #10161f; color: #b9c6d8; }
  body[data-power="off"] #power { border-color: #34435c; background: #10161f; color: #b9c6d8; }
  #power:focus-visible, #cc-toggle:focus-visible { outline: 4px solid #ffd166; outline-offset: 3px; }
</style>
</head>
<body data-power="off">
<div id="reachy-mount"></div>
<div id="orb-fallback"><div class="dot"></div></div>
<div id="cc" hidden>
  <div class="line" id="cc-him" hidden></div>
  <div class="line" id="cc-parker" hidden></div>
  <div class="line" id="cc-source" hidden></div>
</div>
<!-- Truth cards are live regions: offers/outcomes/notices are polite and
     atomic; failures, line errors, and guard redirects are assertive. -->
<div id="card" role="status" aria-live="polite" aria-atomic="true" hidden></div>
<div id="alert" role="alert" aria-atomic="true" hidden></div>
<div id="sr-status" class="sr-only" aria-live="polite"></div>
<div id="bottom-bar">
  <button id="cc-toggle" aria-pressed="false" aria-label="Closed captions">CC</button>
  <button id="power" role="switch" aria-checked="false">
    <span class="lamp"></span><span id="power-label">Turn Parker on</span>
  </button>
</div>

<script src="/parker/converse/static/converse/expression.js"></script>
<script>
'use strict';

// ---------------------------------------------------------------------------
// The companion runtime: power on -> one continuous live line (mic in,
// audio out, spoken confirmation); power off -> everything released and
// persisted off. The semantic expression controller receives only real
// signals, exactly like the lab page — the renderer and the screen-reader
// status read from it.
// ---------------------------------------------------------------------------

const expr = window.ParkerExpression ? ParkerExpression.createController() : null;
let tickTimer = expr ? setInterval(() => { try { expr.tick(); } catch (err) {} }, 500) : null;

function presence(name, data) {
  if (expr) { try { expr.handleEvent(name, data); } catch (err) {} }
}
function presenceEnergy(levels) {
  if (expr) { try { expr.setEnergy(levels); } catch (err) {} }
}

const $ = (id) => document.getElementById(id);
let sessionId = null;      // receipts/journal lane only — no turns here
let ccOn = false;
let powerGen = 0;          // bumps on every power flip; fences late work

// ---------------------------------------------------------------------------
// Cards: the one place non-voice truth appears (action offers/outcomes,
// guard redirects, honest errors). Never a control — nothing to tap.
// ---------------------------------------------------------------------------

// Two live regions, one visible at a time: failures, line errors, and the
// medical redirect are assertive (role=alert); offers, outcomes, and
// notices are polite + atomic so VoiceOver reads the whole readback.
const ALERT_KINDS = {failed: true, error: true, guard: true};
let cardTimer = null;
function showCard(kind, text, ttlMs) {
  const region = $(ALERT_KINDS[kind] ? 'alert' : 'card');
  const other = $(ALERT_KINDS[kind] ? 'card' : 'alert');
  clearTimeout(cardTimer);
  other.hidden = true;
  region.className = kind;
  region.textContent = text;
  region.hidden = false;
  if (ttlMs) cardTimer = setTimeout(() => { region.hidden = true; }, ttlMs);
}
function hideCard() { clearTimeout(cardTimer); $('card').hidden = true; $('alert').hidden = true; }

// ---------------------------------------------------------------------------
// Captions (CC): TV-style, bottom third, expire on their own.
// ---------------------------------------------------------------------------

let himTimer = null, parkerTimer = null;
function caption(which, text) {
  if (!ccOn || !text) return;
  const line = $(which === 'him' ? 'cc-him' : 'cc-parker');
  line.textContent = text;
  line.hidden = false;
  const clear = () => { line.hidden = true; line.textContent = ''; };
  if (which === 'him') { clearTimeout(himTimer); himTimer = setTimeout(clear, 8000); }
  else { clearTimeout(parkerTimer); parkerTimer = setTimeout(clear, 8000); }
}
function appendParkerCaption(text) {
  if (!ccOn || !text) return;
  const line = $('cc-parker');
  const current = line.hidden ? '' : line.textContent;
  const joined = (current ? current + '' : '') + text;
  line.textContent = joined.length > 160 ? '\\u2026' + joined.slice(-160) : joined;
  line.hidden = false;
  clearTimeout(parkerTimer);
  parkerTimer = setTimeout(() => { line.hidden = true; line.textContent = ''; }, 8000);
}
// CC on: "Checked the web" plus bounded source labels (never URLs, never
// spoken). CC off: the Reachy work cue and Parker's own words are the
// whole signal — the zero-chrome contract (independent review, 2026-09-01).
let sourceTimer = null;
function captionSources(items) {
  if (!ccOn || !items || !items.length) return;
  const labels = items.slice(0, 3)
    .map((s) => String(s.label || '').trim().slice(0, 40))
    .filter((l) => l);
  const line = $('cc-source');
  line.textContent = 'Checked the web' + (labels.length ? ' · ' + labels.join(' · ') : '');
  line.hidden = false;
  clearTimeout(sourceTimer);
  sourceTimer = setTimeout(() => { line.hidden = true; line.textContent = ''; }, 12000);
}
function applyCc(on) {
  ccOn = !!on;
  $('cc').hidden = !ccOn;
  $('cc-toggle').setAttribute('aria-pressed', ccOn ? 'true' : 'false');
  if (!ccOn) {
    $('cc-him').hidden = true; $('cc-parker').hidden = true; $('cc-source').hidden = true;
  }
}

// ---------------------------------------------------------------------------
// Audio plumbing (ported from the reviewed lab live lane).
// ---------------------------------------------------------------------------

const TARGET_LIVE_RATE = 24000;

function micEnergy(data) {
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
  return Math.min(1, Math.sqrt(sum / data.length) * 6);
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

function bufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const STEP = 0x8000;
  for (let i = 0; i < bytes.length; i += STEP) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + STEP));
  }
  return btoa(binary);
}

const WAKE_RATE = 16000;

// The ONE microphone owner for the whole powered-on lifetime: acquired on
// the power gesture, released at power off. Frames route by mode —
// 'wake' streams 16 kHz PCM to the LOCAL wake lane (nothing leaves this
// machine while dormant), 'live' streams 24 kHz to the realtime line.
const audio = {stream: null, micCtx: null, playCtx: null, proc: null,
               gain: null, source: null, mode: 'idle'};

// Wake lane state: `tail` is what he said right after "Hey Parker" (the
// wake frame's tail plus any post-wake transcript the lane sends while
// the line connects); it rides the live socket's first frame.
const wake = {ws: null, retried: false, head: '', tail: '', tailTimer: null};

const live = {ws: null, playCtx: null, nextTime: 0, sources: [], chunkMeta: [],
              energyTimer: null, wasPlaying: false, closingSeen: false,
              responseOpen: false, guardSpeaking: 0, retries: 0, revoked: false};
let startingLive = false;

// Power authority lives in the ENGINE (docs/plans/2026-09-01-foundation-
// closure-overnight.md): the page claims power, receives an owner token +
// generation, and every wake/realtime socket presents them. The switch
// shows ON only after the engine acknowledged; off releases everything
// here first and then persists, retrying a failed write out loud.
const clientId = (window.crypto && crypto.randomUUID)
  ? crypto.randomUUID() : ('page-' + Math.random().toString(36).slice(2));
const power = {token: null, gen: 0};
let offSaveTimer = null;
function powerQuery() {
  return '?owner=' + encodeURIComponent(power.token || '') + '&gen=' + power.gen;
}

// Listening may only be claimed when the provider response is done AND
// everything scheduled actually played (or was flushed) AND no guard
// speech is audible (independent review, 2026-09-01).
function maybeOutputDrained() {
  if (!live.ws) return;
  if (live.responseOpen || live.guardSpeaking > 0 || live.wasPlaying) return;
  if (live.playCtx && live.chunkMeta.length) return;
  presence('assistant_audio_drained');
}

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
        maybeOutputDrained();
      }
    }
  }, 120);
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
    const buffer = live.playCtx.createBuffer(1, floats.length, TARGET_LIVE_RATE);
    buffer.getChannelData(0).set(floats);
    const src = live.playCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(live.playCtx.destination);
    const at = Math.max(live.playCtx.currentTime + 0.05, live.nextTime);
    src.start(at);
    live.nextTime = at + buffer.duration;
    live.sources.push(src);
    src.onended = () => { live.sources = live.sources.filter((s) => s !== src); };
    let sum = 0;
    for (let i = 0; i < floats.length; i++) sum += floats[i] * floats[i];
    live.chunkMeta.push({
      at: at, dur: buffer.duration,
      energy: Math.min(1, Math.sqrt(sum / floats.length) * 4),
    });
    presence('assistant_audio');
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
  // Guard speech joins the one output lifecycle: fenced to this socket,
  // counted by the drain gate, cancelled by power-off/page-hide.
  if (!text || !window.speechSynthesis) return;
  const ws = live.ws;
  if (!ws) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95;
  live.guardSpeaking += 1;
  utterance.onstart = () => {
    if (ws !== live.ws) { speechSynthesis.cancel(); return; }
    presence('assistant_audio');
    presenceEnergy({parker: 0.6});
  };
  const settle = () => {
    if (ws !== live.ws) return;
    live.guardSpeaking = Math.max(0, live.guardSpeaking - 1);
    presenceEnergy({parker: 0});
    maybeOutputDrained();
  };
  utterance.onend = settle;
  utterance.onerror = settle;
  speechSynthesis.speak(utterance);
}

async function acquireAudio() {
  if (audio.stream) return true;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true},
    });
  } catch (err) {
    return false;
  }
  audio.stream = stream;
  audio.playCtx = new (window.AudioContext || window.webkitAudioContext)();
  audio.micCtx = new (window.AudioContext || window.webkitAudioContext)();
  audio.source = audio.micCtx.createMediaStreamSource(stream);
  audio.proc = audio.micCtx.createScriptProcessor(4096, 1, 1);
  audio.gain = audio.micCtx.createGain();
  audio.gain.gain.value = 0;
  audio.proc.onaudioprocess = (event) => {
    const data = event.inputBuffer.getChannelData(0);
    if (audio.mode === 'wake') {
      // Dormant: frames go ONLY to the local wake lane — no cloud, no
      // presence energy (a lifeless robot does not react to the room).
      if (wake.ws && wake.ws.readyState === 1) {
        const pcm = resamplePCM16(data, audio.micCtx.sampleRate, WAKE_RATE);
        try { wake.ws.send(JSON.stringify({type: 'audio', data: bufferToBase64(pcm.buffer)})); } catch (err) {}
      }
    } else if (audio.mode === 'live') {
      if (!live.ws || live.ws.readyState !== 1) return;
      presenceEnergy({user: micEnergy(data)});
      const pcm = resamplePCM16(data, audio.micCtx.sampleRate, TARGET_LIVE_RATE);
      try { live.ws.send(JSON.stringify({type: 'audio', data: bufferToBase64(pcm.buffer)})); } catch (err) {}
    }
  };
  audio.source.connect(audio.proc);
  audio.proc.connect(audio.gain);
  audio.gain.connect(audio.micCtx.destination);
  return true;
}

function releaseAudio() {
  audio.mode = 'idle';
  try { audio.proc && audio.proc.disconnect(); audio.gain && audio.gain.disconnect(); } catch (err) {}
  try { audio.stream && audio.stream.getTracks().forEach((track) => track.stop()); } catch (err) {}
  try { audio.micCtx && audio.micCtx.close(); } catch (err) {}
  try { audio.playCtx && audio.playCtx.close(); } catch (err) {}
  audio.stream = null; audio.micCtx = null; audio.playCtx = null;
  audio.proc = null; audio.gain = null; audio.source = null;
}

// The wake acknowledgment: two quick rising notes — "I heard you" —
// while the eyes are already popping open. The greeting follows.
function chirp() {
  try {
    const ctx = audio.playCtx;
    if (!ctx) return;
    const now = ctx.currentTime;
    const gain = ctx.createGain();
    gain.gain.value = 0.06;
    gain.connect(ctx.destination);
    [660, 990].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      osc.frequency.value = freq;
      osc.type = 'sine';
      osc.connect(gain);
      osc.start(now + i * 0.09);
      osc.stop(now + i * 0.09 + 0.09);
    });
  } catch (err) { /* sound is a courtesy */ }
}

// Ends the CLOUD line only: the microphone and contexts stay owned for
// the powered-on lifetime (dormancy re-arms wake on the same stream).
function endLine() {
  startingLive = false;
  const ws = live.ws;
  live.ws = null;
  if (live.energyTimer) { clearInterval(live.energyTimer); live.energyTimer = null; }
  flushLivePlayback();
  try { window.speechSynthesis && speechSynthesis.cancel(); } catch (err) {}
  live.guardSpeaking = 0;
  live.responseOpen = false;
  live.closingSeen = false;
  live.playCtx = null; // alias only — audio.playCtx stays open
  if (ws) {
    try { ws.send(JSON.stringify({type: 'end'})); } catch (err) {}
    try { ws.close(); } catch (err) {}
  }
}

function stopWakeLane() {
  const ws = wake.ws;
  wake.ws = null;
  if (ws) {
    try { ws.send(JSON.stringify({type: 'end'})); } catch (err) {}
    try { ws.close(); } catch (err) {}
  }
}

// ---------------------------------------------------------------------------
// Live events -> presence, captions, cards. Spoken confirmation only:
// no frame here ever asks him to tap anything.
// ---------------------------------------------------------------------------

function handleLiveEvent(event) {
  if (event.type === 'audio') {
    live.responseOpen = true;
    playLivePcm(event.data);
  } else if (event.type === 'response_state') {
    if (event.status === 'done') {
      live.responseOpen = false;
      maybeOutputDrained();
    }
  } else if (event.type === 'user_transcript') {
    presence('user_transcript');
    caption('him', event.text);
    $('cc-parker').hidden = true; $('cc-parker').textContent = '';
  } else if (event.type === 'assistant_transcript_delta') {
    appendParkerCaption(event.text);
  } else if (event.type === 'working') {
    const status = event.status === 'started' ? 'work_start'
      : event.status === 'failed' ? 'work_failed' : 'work_done';
    presence(status, {kind: event.kind || 'search'});
  } else if (event.type === 'clear') {
    const flushed = flushLivePlayback();
    const thinkingCancelled = expr && expr.getState().phase === 'thinking';
    if (flushed || thinkingCancelled) presence('interrupted');
  } else if (event.type === 'guard_redirect') {
    presence('guard_redirect');
    flushLivePlayback();
    showCard('guard', event.text, 20000);
    speakNow(event.text);
  } else if (event.type === 'proposal_staged') {
    presence('proposal_staged');
    showCard('staged',
      'Parker wants to set up: ' + (event.readback || event.label || 'an action')
      + ' \\u2014 say \\u201Cyes\\u201D to do it, or \\u201Cno\\u201D to cancel.', 0);
  } else if (event.type === 'action_result') {
    const label = event.label || 'that';
    if (event.status === 'executed') {
      presence('action_executed');
      showCard('executed', 'Done \\u2014 ' + label + '.', 10000);
    } else if (event.status === 'failed') {
      presence('action_failed');
      showCard('failed',
        'That didn\\u2019t go through \\u2014 ' + label + '. It\\u2019s on the family review page.', 20000);
    } else if (event.status === 'cancelled') {
      presence('attention_resolved');
      showCard('cancelled', 'Cancelled \\u2014 nothing will run.', 6000);
    } else { // expired / replaced: the offer simply lapses
      presence('attention_resolved');
      hideCard();
    }
  } else if (event.type === 'sources') {
    live.sources = event.items || [];
    captionSources(live.sources); // CC on only; CC off keeps zero chrome
  } else if (event.type === 'revoked') {
    // The engine ended this page's authority (someone turned Parker off, or
    // another screen took over): not a line drop — no retry, honest card.
    live.revoked = true;
    onRevoked(event);
  } else if (event.type === 'closing') {
    // The gentle wind-down finished (one wrap-up, one goodbye): let the
    // scheduled audio play out, then return to DORMANT — power stays on,
    // only local wake listening remains.
    presence('closing');
    live.closingSeen = true;
    const remaining = live.playCtx
      ? Math.max(0, (live.nextTime - live.playCtx.currentTime) * 1000)
      : 0;
    const wsAtClosing = live.ws; // a stale timer must never end a NEW session
    setTimeout(() => {
      if (live.ws === wsAtClosing && live.ws) returnToDormancy();
    }, remaining + 300);
  } else if (event.type === 'notice') {
    showCard('notice', event.text || '', 8000);
  } else if (event.type === 'unavailable') {
    endLine();
    presence('offline');
    setPowerVisual('error');
    showCard('error', event.text || 'Live conversation is not available right now.', 0);
  }
}

// ---------------------------------------------------------------------------
// Power: the one real control. Persisted server-side; off must be OFF.
// ---------------------------------------------------------------------------

function setPowerVisual(state) {
  document.body.dataset.power = state;
  const label = $('power-label');
  const on = state === 'on' || state === 'dormant' || state === 'elsewhere';
  $('power').setAttribute('aria-checked', on ? 'true' : 'false'); // 'starting' is not yet on
  // Dormant must read as ASLEEP at a glance, never as "engaged"
  // (Pras, session 3: powered-on-resting vs listening were confusable).
  label.textContent = state === 'on' ? 'Parker is on'
    : state === 'dormant' ? 'Resting — say “Hey Parker”'
    : state === 'starting' ? 'Waking…'
    : state === 'error' ? 'Try again'
    : state === 'elsewhere' ? 'On another screen'
    : 'Turn Parker on';
  updateSrStatus();
}

function persistSettings(fields) {
  // CC only — power is never written here (the engine owns it).
  try {
    fetch('/parker/converse/companion/settings', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(fields),
    }).catch(() => {});
  } catch (err) { /* CC persistence is best-effort */ }
}

async function claimPower() {
  let res;
  try {
    res = await fetch('/parker/converse/companion/power', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({on: true, client_id: clientId}),
    });
  } catch (err) { return {ok: false, reason: 'unreachable'}; }
  if (res.status === 409) return {ok: false, reason: 'elsewhere'};
  if (!res.ok) return {ok: false, reason: 'not_saved'};
  let data;
  try { data = await res.json(); } catch (err) { return {ok: false, reason: 'not_saved'}; }
  if (!data || !data.owner) return {ok: false, reason: 'not_saved'};
  power.token = data.owner;
  power.gen = data.gen;
  return {ok: true};
}

// Off is already true on this screen before this runs; the engine call
// revokes every other screen and persists. A failed write is retried a
// bounded number of times and then said out loud — never swallowed.
const OFF_SAVE_DELAYS = [1000, 3000, 8000];
function releasePower(attempt) {
  clearTimeout(offSaveTimer);
  const genAtCall = powerGen;
  const failed = () => {
    if (genAtCall !== powerGen) return;
    if (attempt < OFF_SAVE_DELAYS.length) {
      offSaveTimer = setTimeout(() => releasePower(attempt + 1), OFF_SAVE_DELAYS[attempt]);
    } else {
      showCard('error', 'Parker is off on this screen, but the setting didn’t save — it may come back on after a restart.', 0);
    }
  };
  let pending;
  try {
    pending = fetch('/parker/converse/companion/power', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({on: false, client_id: clientId}),
    });
  } catch (err) { failed(); return; }
  pending.then(async (res) => {
    if (!res.ok) { failed(); return; }
    let data = null;
    try { data = await res.json(); } catch (err) {}
    if (!data || data.saved !== true) failed();
  }).catch(failed);
}

let reclaimedGen = -1;
async function onRevoked(event) {
  const reason = event && event.reason;
  if (!powered() && document.body.dataset.power !== 'elsewhere') return;
  if (reason === 'power_off' && reclaimedGen !== powerGen && audio.stream && !live.ws) {
    // The engine may simply have restarted (nobody owns power after a
    // restart, but the durable switch still says ON): if so, re-claim once
    // and carry on resting — instead of telling him he was turned off.
    reclaimedGen = powerGen;
    const genAtCheck = powerGen;
    let settings = null;
    try {
      const res = await fetch('/parker/converse/companion/settings');
      if (res.ok) settings = await res.json();
    } catch (err) { /* engine still down: fall through to honest off */ }
    if (genAtCheck !== powerGen) return;
    if (settings && settings.power_on && !settings.owner_client) {
      const claim = await claimPower();
      if (genAtCheck !== powerGen) return;
      if (claim.ok) { stopWakeLane(); wake.retried = false; startDormant(); return; }
    }
  }
  powerOff({silent: true}); // the engine already turned us off; do not turn off the new owner
  showCard('notice', reason === 'power_off'
    ? 'Parker was turned off.'
    : 'Parker is on another screen now.', 0);
}

async function powerOn(options) {
  if (startingLive || live.ws || wake.ws) return;
  const fromBoot = !!(options && options.fromBoot);
  startingLive = true;
  powerGen++;
  const myGen = powerGen;
  clearTimeout(offSaveTimer);
  hideCard();
  setPowerVisual('starting');
  live.retries = 0;
  let claim = await claimPower();
  if (myGen !== powerGen) { startingLive = false; return; }
  if (!claim.ok && claim.reason === 'elsewhere' && fromBoot) {
    // A reload: the previous page's wake socket can still be registered for
    // one inference (<1 s). One patient retry before believing "elsewhere".
    await new Promise((resolve) => setTimeout(resolve, 1500));
    if (myGen !== powerGen) { startingLive = false; return; }
    claim = await claimPower();
    if (myGen !== powerGen) { startingLive = false; return; }
  }
  if (!claim.ok) {
    // Nothing is on. The switch stays off (or shows where Parker IS on).
    startingLive = false;
    if (claim.reason === 'elsewhere') {
      setPowerVisual('elsewhere');
      showCard('notice', 'Parker is on another screen. Use the switch here to turn it off everywhere.', 0);
    } else {
      presence('error');
      setPowerVisual('off');
      showCard('error', claim.reason === 'unreachable'
        ? 'Parker can’t reach its engine right now — try the switch again in a moment.'
        : 'Parker couldn’t save the switch — nothing is on. Try again.', 0);
    }
    return;
  }
  const granted = await acquireAudio();
  startingLive = false;
  if (myGen !== powerGen) { if (granted && !live.ws && !wake.ws) releaseAudio(); return; }
  if (!granted) {
    presence('error');
    setPowerVisual('error');
    showCard('error',
      fromBoot
        ? 'Turn the switch to wake Parker.'
        : 'Parker can\u2019t use the microphone \u2014 it needs permission on this computer.', 0);
    return;
  }
  if (fromBoot && (audio.playCtx.state === 'suspended' || audio.micCtx.state === 'suspended')) {
    // Restored power without a user gesture: the browser keeps audio
    // suspended, so a truthful wake needs one flip of the switch.
    releaseAudio();
    setPowerVisual('off');
    showCard('notice', 'Turn the switch to wake Parker.', 0);
    return;
  }
  startDormant();
}

function powered() {
  const state = document.body.dataset.power;
  return state === 'dormant' || state === 'on' || state === 'starting';
}
function switchedOn() {
  const state = document.body.dataset.power;
  return powered() || state === 'error' || state === 'elsewhere';
}

// ---------------------------------------------------------------------------
// Dormancy: powered but lifeless. The mic feeds ONLY the local wake lane;
// the cloud line is closed; "Hey Parker" is the one way to wake
// (docs/plans/2026-09-01-wake-word.md).
// ---------------------------------------------------------------------------

function startDormant() {
  if (!audio.stream) return;
  audio.mode = 'wake';
  presence('dormant');
  setPowerVisual('dormant');
  const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
  const ws = new WebSocket(scheme + location.host + '/parker/converse/wake' + powerQuery());
  wake.ws = ws;
  ws.onmessage = (message) => {
    if (ws !== wake.ws) return;
    let event;
    try { event = JSON.parse(message.data); } catch (err) { return; }
    if (!event || typeof event !== 'object') return;
    if (event.type === 'wake') onWake(event);
    else if (event.type === 'tail') {
      // More of his same-breath request, transcribed while the line connects.
      // The engine cleared its window at the wake, so these frames hold only
      // what came AFTER it: keep the wake frame's own words in front.
      if (typeof event.text === 'string' && event.text) {
        wake.tail = ((wake.head ? wake.head + ' ' : '') + event.text).trim().slice(0, 200);
      }
    } else if (event.type === 'revoked') {
      onRevoked(event);
    } else if (event.type === 'unavailable') {
      // No local wake model: FAIL CLOSED. Dormant privacy promised no cloud
      // audio until "Hey Parker"; without a way to hear that, Parker stays
      // off and says why (independent review, 2026-09-01).
      stopWakeLane();
      powerOff();
      showCard('error', 'Wake listening needs the local voice model on this computer, so Parker stayed off. Ask the family to run make voice-deps.', 0);
    }
  };
  ws.onclose = () => {
    if (ws !== wake.ws) return;
    wake.ws = null;
    if (audio.mode !== 'wake' || !powered()) return;
    if (live.ws || startingLive) return; // a line is opening — not dormancy's business
    if (!wake.retried) {
      wake.retried = true;
      setTimeout(() => {
        if (audio.mode === 'wake' && powered() && !wake.ws) startDormant();
      }, 1500);
    } else {
      presence('error');
      setPowerVisual('error');
      showCard('error', 'Wake listening hiccuped — flip the switch to try again.', 0);
    }
  };
  ws.onerror = () => { if (ws === wake.ws) { try { ws.close(); } catch (err) {} } };
}

function onWake(event) {
  wake.retried = false;
  live.retries = 0; // his wake is the interaction that re-arms one retry
  wake.head = (event && typeof event.tail === 'string') ? event.tail.trim().slice(0, 120) : '';
  wake.tail = wake.head;
  presence('wake_detected'); // the POP: eyes snap open, antennae perk
  chirp();
  // The wake lane stays open (mic frames keep going to it) so the rest of
  // "Hey Parker, can you help me" is transcribed while the line connects;
  // the line's open ends it, or this bound does.
  clearTimeout(wake.tailTimer);
  wake.tailTimer = setTimeout(() => { if (wake.ws) stopWakeLane(); }, 3000);
  startActive();
}

// ---------------------------------------------------------------------------
// The active session: the realtime line over the already-held microphone.
// ---------------------------------------------------------------------------

function startActive() {
  if (live.ws) return;
  // Mic frames keep feeding the wake lane's tail until the line is OPEN;
  // every other entry (drop-retry) streams live from here.
  if (!wake.ws) audio.mode = 'live';
  // Truthful phase on EVERY entry path: the wake pop already sits in
  // 'connecting', where this is a no-op — but the drop-retry arrives from
  // error and must not stream while posing as rest (page-pin suite).
  presence('connect', {mode: 'live'});
  live.playCtx = audio.playCtx;
  live.revoked = false;
  const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
  const ws = new WebSocket(scheme + location.host + '/parker/converse/realtime' + powerQuery());
  live.ws = ws;
  watchLivePlayback();

  ws.onopen = () => {
    if (ws !== live.ws) return; // stale open must not restore live state
    // The handoff contract: hello (with the tail) is the FIRST frame, then
    // the wake lane ends and the mic streams to this line.
    try { ws.send(JSON.stringify({type: 'hello', tail: wake.tail || ''})); } catch (err) {}
    wake.tail = ''; wake.head = '';
    clearTimeout(wake.tailTimer);
    stopWakeLane();
    audio.mode = 'live';
    presence('connected');
    setPowerVisual('on');
  };
  ws.onmessage = (message) => {
    if (ws !== live.ws) return;
    let event;
    try { event = JSON.parse(message.data); } catch (err) { return; }
    if (!event || typeof event !== 'object') return;
    handleLiveEvent(event);
  };
  ws.onclose = () => {
    if (ws !== live.ws) return;
    if (live.revoked) return; // handled by onRevoked — never a retry
    if (live.closingSeen) returnToDormancy();
    else lineDropped();
  };
  ws.onerror = () => { if (ws === live.ws && !live.revoked) lineDropped(); };
}

// The session wound down naturally (wrap-up -> goodbye -> closing): back
// to dormant — power stays on, only local wake listening remains.
function returnToDormancy() {
  endLine();
  flushPresenceReceipts();
  if (powered() && audio.stream) startDormant();
}

let retryTimer = null;
function lineDropped() {
  // The line failed while active: ONE quiet retry per activation, then an
  // honest note and back to rest — local wake still works, so "Hey Parker"
  // is the way back. Never a reconnect loop (independent review, 2026-09-01).
  const genAtDrop = powerGen;
  endLine();
  if (live.retries >= 1) {
    live.retries = 0;
    showCard('notice', 'The line dropped. Say “Hey Parker” to try again.', 12000);
    if (powered() && audio.stream) startDormant();
    else { presence('error'); setPowerVisual('error'); }
    return;
  }
  live.retries += 1;
  presence('error');
  setPowerVisual('error');
  showCard('error', 'The line dropped — Parker is reconnecting…', 0);
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => {
    if (genAtDrop !== powerGen) return; // the switch moved meanwhile
    if (document.body.dataset.power !== 'error') return;
    hideCard();
    startActive();
  }, 2500);
}

function powerOff(options) {
  const silent = !!(options && options.silent); // revoked by the engine: it is already off
  powerGen++;
  clearTimeout(retryTimer);
  clearTimeout(offSaveTimer);
  clearTimeout(wake.tailTimer);
  flushPresenceReceipts();
  stopWakeLane();
  endLine();
  releaseAudio();
  wake.retried = false;
  wake.tail = ''; wake.head = '';
  live.retries = 0;
  hideCard();
  presence('stopped');
  presence('offline');
  setPowerVisual('off');
  power.token = null;
  if (!silent) releasePower(0);
}

$('power').addEventListener('click', () => {
  if (switchedOn()) powerOff();
  else powerOn();
});
$('cc-toggle').addEventListener('click', () => {
  applyCc(!ccOn);
  persistSettings({cc_on: ccOn});
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') powerOff(); // the keyboard's big way out
});

// ---------------------------------------------------------------------------
// Screen-reader status: the semantic state, in words, without visible chrome.
// ---------------------------------------------------------------------------

function updateSrStatus() {
  if (!expr) return;
  const state = document.body.dataset.power;
  $('sr-status').textContent =
    state === 'off' ? 'Parker is off. Nothing is listening.'
    : state === 'error' ? 'Parker hit a snag. Use the power switch to try again.'
    : state === 'elsewhere' ? 'Parker is on another screen. Use the switch here to turn it off everywhere.'
    : ParkerExpression.describe(expr.getState());
}

// ---------------------------------------------------------------------------
// Semantic transition receipts (same contract as the lab page): stream
// over the live socket for the session journal; flush the local buffer
// through the receipts beacon at power-off/page-hide.
// ---------------------------------------------------------------------------

const PRESENCE_RECEIPT_CAP = 300;
const presenceReceipts = [];
let presenceDropped = 0;
let prevPresence = null;

function recordPresenceTransition(next, cause) {
  const entry = {
    at_ms: Math.round(performance.now()),
    gen: powerGen,
    from: prevPresence ? prevPresence.phase : '',
    to: next.phase,
    work: next.work.join(','),
    action: next.action,
    guard: next.guard,
    attention: next.attention,
    reason: cause || '',
  };
  prevPresence = next;
  if (presenceReceipts.length >= PRESENCE_RECEIPT_CAP) presenceDropped += 1;
  else presenceReceipts.push(entry);
  if (live.ws && live.ws.readyState === 1) {
    try {
      live.ws.send(JSON.stringify(Object.assign({type: 'expression'}, entry)));
    } catch (err) { /* receipts are best-effort */ }
  }
}

function postReceipt(marks) {
  if (!sessionId) return false; // the boot session is still being opened
  const body = JSON.stringify(marks);
  try {
    navigator.sendBeacon(
      '/parker/converse/sessions/' + sessionId + '/receipts',
      new Blob([body], {type: 'application/json'})
    );
  } catch (err) { /* best-effort */ }
  return true;
}

function flushPresenceReceipts() {
  if (!presenceReceipts.length || !sessionId) return;
  postReceipt({
    expression: presenceReceipts.slice(),
    expression_dropped: presenceDropped,
  });
  presenceReceipts.length = 0;
  presenceDropped = 0;
}

if (expr) {
  prevPresence = expr.getState();
  expr.subscribe((s, cause) => {
    updateSrStatus();
    recordPresenceTransition(s, cause);
  });
}
window.ParkerPresence = {controller: expr};
window.ParkerReceipts = {post: postReceipt};

// ---------------------------------------------------------------------------
// Page teardown: leaving the page releases everything (independent
// review, 2026-09-01) — idempotent, BFCache restores reload clean.
// ---------------------------------------------------------------------------

let pageReleased = false;
function releasePage() {
  if (pageReleased) return;
  pageReleased = true;
  powerGen++;
  clearTimeout(retryTimer);
  clearTimeout(offSaveTimer);
  clearTimeout(wake.tailTimer);
  clearTimeout(cardTimer);
  stopWakeLane();
  endLine();
  releaseAudio();
  try { window.speechSynthesis && speechSynthesis.cancel(); } catch (err) {}
  if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
  const scene = window.ParkerPresence && window.ParkerPresence.scene;
  if (scene) {
    try { scene.dispose(); } catch (err) {}
    window.ParkerPresence.scene = null;
  }
}
window.addEventListener('pagehide', () => {
  flushPresenceReceipts();
  releasePage();
  if (sessionId) {
    try {
      navigator.sendBeacon('/parker/converse/sessions/' + sessionId + '/end', new Blob(['{}'], {type: 'application/json'}));
    } catch (err) {}
  }
});
window.addEventListener('pageshow', (event) => {
  if (event.persisted && pageReleased) location.reload();
});

// ---------------------------------------------------------------------------
// Boot: restore persisted power/CC, open a receipts session, and if the
// power was on, wake up (a browser without a stored gesture shows one
// honest "turn the switch" note instead of a silent half-on state).
// ---------------------------------------------------------------------------

(async () => {
  presence('offline'); // off until proven on
  try {
    const res = await fetch('/parker/converse/sessions', {method: 'POST'});
    if (res.ok) sessionId = (await res.json()).session_id;
  } catch (err) { /* receipts stay local-only */ }
  let settings = {power_on: false, cc_on: false};
  try {
    const res = await fetch('/parker/converse/companion/settings');
    if (res.ok) settings = await res.json();
  } catch (err) { /* default to off — never silently listen */ }
  applyCc(!!settings.cc_on);
  if (settings.power_on) powerOn({fromBoot: true});
  else setPowerVisual('off');
})();
</script>
<script type="module">
// The Reachy scene boots independently: a slow import or WebGL failure
// never delays the line, and on failure the breathing dot carries state.
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
      document.getElementById('orb-fallback').hidden = true;
      window.ParkerPresence.scene = scene;
      sceneReceipt('webgl_ready');
    } else {
      sceneReceipt('webgl_fallback');
    }
  } catch (err) { sceneReceipt('webgl_fallback'); /* no WebGL / no module: the dot remains the presence */ }
})();
// One aggregate receipt so the engine's own records say whether THIS
// screen rendered the Reachy or fell back to the dot — the packaged
// WKWebView lifecycle is judged from evidence, not screenshots.
function sceneReceipt(outcome) {
  // The scene usually boots before the receipts session id exists (the
  // packaged WKWebView probe caught exactly that): retry until it sends.
  let attempts = 0;
  const send = () => {
    const sent = !!(window.ParkerReceipts && window.ParkerReceipts.post
      && window.ParkerReceipts.post({outcome}));
    if (!sent && attempts++ < 20) setTimeout(send, 500);
  };
  send();
}
</script>
</body>
</html>
"""
