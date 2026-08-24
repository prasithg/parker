"""Patient-facing voice-practice page.

The page is deliberately self-contained: vanilla Web Audio/MediaRecorder APIs,
large manual controls, and the local Parker attempt API. A target duration is
guidance only; the person always decides when to stop and when to continue.
"""

PRACTICE_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'">
<title>Parker Voice Practice</title>
<style>
  :root {
    color-scheme: light;
    --ink: #172033;
    --muted: #657089;
    --paper: #f4f6fb;
    --surface: #ffffff;
    --soft: #e8ecf5;
    --accent: #315fef;
    --accent-dark: #2447bb;
    --good: #23875d;
    --warm: #f2b84b;
    --danger: #b43e4a;
    --focus: #0a66ff;
    --shadow: 0 20px 60px rgba(32, 49, 85, .10);
  }
  * { box-sizing: border-box; }
  html { min-height: 100%; background: var(--paper); }
  body {
    min-height: 100vh;
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: var(--ink);
    background:
      radial-gradient(circle at 12% -8%, rgba(49, 95, 239, .12), transparent 34rem),
      var(--paper);
    letter-spacing: -.012em;
    overflow-x: hidden;
  }
  button, input { font: inherit; }
  button:focus-visible, input:focus-visible { outline: 4px solid rgba(10, 102, 255, .34); outline-offset: 4px; }
  [hidden] { display: none !important; }
  .shell { width: min(1100px, 100%); margin: 0 auto; padding: max(24px, 4vw); }
  header { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-bottom: 34px; }
  .brand { display: flex; align-items: center; gap: 14px; }
  .mark {
    width: 48px; height: 48px; border-radius: 16px; display: grid; place-items: center;
    color: white; background: var(--ink); font-weight: 750; font-size: 21px;
  }
  .brand strong { display: block; font-size: 21px; }
  .brand span { display: block; color: var(--muted); font-size: 15px; margin-top: 2px; }
  .rounds { display: flex; align-items: center; gap: 9px; color: var(--muted); font-weight: 650; }
  .pip { width: 17px; height: 17px; border-radius: 50%; background: #d9dfeb; }
  .pip.done { background: var(--good); box-shadow: inset 0 0 0 4px white; border: 2px solid var(--good); }
  .pip.current { background: var(--accent); box-shadow: 0 0 0 5px rgba(49, 95, 239, .14); }

  .grid { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(280px, .75fr); gap: 28px; align-items: start; min-width: 0; }
  .grid > *, .brand, .brand > div, .consent > span { min-width: 0; }
  .consent > span { overflow-wrap: anywhere; }
  .card { background: var(--surface); border-radius: 28px; box-shadow: var(--shadow); }
  .practice { min-height: 650px; padding: clamp(28px, 5vw, 58px); display: flex; flex-direction: column; }
  .eyebrow { color: var(--accent); font-weight: 750; letter-spacing: .09em; text-transform: uppercase; font-size: 14px; }
  h1 { font-size: clamp(42px, 7vw, 76px); line-height: .98; letter-spacing: -.055em; margin: 18px 0 20px; max-width: 760px; }
  .lead { font-size: clamp(21px, 3vw, 29px); line-height: 1.34; color: #3e4b65; margin: 0 0 28px; max-width: 720px; }
  .pace-note { display: flex; gap: 12px; align-items: flex-start; padding: 16px 18px; background: #eef3ff; color: #2b477d; border-radius: 16px; font-size: 17px; line-height: 1.45; }
  .pace-note b { color: #19325f; }
  .dot-icon { width: 24px; height: 24px; flex: 0 0 24px; border-radius: 50%; background: var(--accent); color: white; display: grid; place-items: center; font-size: 14px; font-weight: 800; }
  .consent {
    display: flex; gap: 14px; align-items: flex-start; margin: 28px 0 0; padding: 18px;
    border: 2px solid var(--soft); border-radius: 18px; cursor: pointer;
  }
  .consent input { width: 28px; height: 28px; accent-color: var(--accent); flex: 0 0 auto; }
  .consent strong { display: block; font-size: 18px; }
  .consent span { display: block; color: var(--muted); line-height: 1.4; margin-top: 3px; }
  .actions { margin-top: auto; padding-top: 34px; display: flex; gap: 14px; flex-wrap: wrap; }
  .button {
    min-height: 68px; border: 0; border-radius: 999px; padding: 0 30px; cursor: pointer;
    font-size: 21px; font-weight: 720; transition: transform .12s ease, background .12s ease;
  }
  .button:active { transform: scale(.98); }
  .button.primary { background: var(--accent); color: white; min-width: 220px; }
  .button.primary:hover { background: var(--accent-dark); }
  .button.stop { background: var(--danger); color: white; min-width: 220px; }
  .button.secondary { background: var(--soft); color: var(--ink); }
  .button:disabled { cursor: wait; opacity: .58; }

  .live { text-align: center; flex: 1; display: flex; flex-direction: column; justify-content: center; }
  .live-label { color: var(--muted); font-size: 20px; font-weight: 650; }
  #elapsed { font-variant-numeric: tabular-nums; font-size: clamp(76px, 14vw, 142px); line-height: 1; letter-spacing: -.07em; margin: 16px 0 30px; }
  .meter { position: relative; height: 34px; border-radius: 999px; overflow: hidden; background: #e1e6f0; margin: 0 auto; width: min(560px, 100%); }
  .meter-fill { height: 100%; width: 0; border-radius: inherit; background: linear-gradient(90deg, var(--warm), var(--good)); transition: width 70ms linear; }
  .meter-target { position: absolute; top: 0; bottom: 0; left: 50%; border-left: 4px solid rgba(23, 32, 51, .48); }
  .meter-labels { display: flex; justify-content: space-between; width: min(560px, 100%); margin: 10px auto 0; color: var(--muted); font-size: 15px; }
  #live-guidance { min-height: 1.6em; color: var(--good); font-size: 20px; font-weight: 680; margin: 24px 0 0; }

  .result { flex: 1; display: flex; flex-direction: column; justify-content: center; }
  .result h2 { font-size: clamp(38px, 6vw, 64px); line-height: 1.04; letter-spacing: -.045em; margin: 0 0 26px; }
  .stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
  .stat { background: var(--paper); padding: 20px; border-radius: 18px; }
  .stat b { display: block; font-size: 31px; font-variant-numeric: tabular-nums; }
  .stat span { color: var(--muted); }
  .rating { margin-top: 28px; }
  .rating > span { display: block; color: var(--muted); margin-bottom: 12px; font-size: 17px; }
  .rating-row { display: flex; flex-wrap: wrap; gap: 10px; }
  .rating button { min-height: 54px; border-radius: 999px; border: 2px solid var(--soft); background: white; padding: 0 22px; font-weight: 680; cursor: pointer; }
  .rating button.selected { border-color: var(--accent); color: var(--accent); background: #eef3ff; }

  aside { display: grid; gap: 20px; }
  .side-card { padding: 26px; }
  .side-card h2 { font-size: 24px; margin: 0 0 14px; letter-spacing: -.035em; }
  .side-card p { color: var(--muted); line-height: 1.48; margin: 0; }
  .steps { list-style: none; padding: 0; margin: 20px 0 0; display: grid; gap: 16px; }
  .steps li { display: grid; grid-template-columns: 36px 1fr; gap: 12px; align-items: start; line-height: 1.4; }
  .step-num { width: 36px; height: 36px; border-radius: 12px; background: var(--ink); color: white; display: grid; place-items: center; font-weight: 750; }
  .history { display: grid; gap: 10px; margin-top: 14px; }
  .history-row { display: flex; justify-content: space-between; gap: 12px; padding: 12px 0; border-top: 1px solid var(--soft); color: var(--muted); }
  .history-row b { color: var(--ink); font-variant-numeric: tabular-nums; }
  .fine { font-size: 14px; color: var(--muted); line-height: 1.48; margin-top: 18px; }
  .status { min-height: 1.5em; color: var(--muted); margin: 16px 0 0; font-weight: 620; }
  .status.error { color: var(--danger); }

  @media (max-width: 820px) {
    .shell { padding: 20px; }
    header { align-items: flex-start; }
    .brand span { display: none; }
    .rounds > span:first-child { display: none; }
    .grid { grid-template-columns: minmax(0, 1fr); }
    .practice { min-height: 680px; border-radius: 24px; }
    aside { grid-template-columns: 1fr; }
  }
  @media (max-width: 520px) {
    .shell { padding: 14px; }
    header { margin: 8px 4px 20px; }
    .mark { width: 42px; height: 42px; border-radius: 14px; }
    .practice { min-height: calc(100vh - 112px); padding: 26px 22px; }
    h1 { font-size: 49px; }
    .lead { font-size: 22px; }
    .button { width: 100%; min-height: 72px; }
    .stats { grid-template-columns: 1fr; }
    aside { display: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
  }
</style>
</head>
<body>
<div class="shell">
  <header>
    <div class="brand">
      <div class="mark" aria-hidden="true">P</div>
      <div><strong>Parker Voice Practice</strong><span>A short daily practice, at your pace</span></div>
    </div>
    <div class="rounds" aria-label="Round progress">
      <span>Today</span><i class="pip current"></i><i class="pip"></i><i class="pip"></i>
    </div>
  </header>

  <div class="grid">
    <main class="card practice">
      <section id="intro">
        <div class="eyebrow">Round <span id="round-number">1</span> of 3</div>
        <h1>Sustained ah</h1>
        <p class="lead">Take a comfortable breath. Then say <strong>“ah”</strong> in one steady, comfortable voice.</p>
        <div class="pace-note">
          <span class="dot-icon" aria-hidden="true">✓</span>
          <span><b>You choose when to stop.</b> Ten seconds is a guide, not a deadline. Nothing moves on until you are ready.</span>
        </div>
        <label class="consent" for="save-audio">
          <input id="save-audio" type="checkbox">
          <span><strong>Keep this short recording on this Parker for review and future personalization</strong><span id="retention-note">Optional. This version does not train from it yet. Never uploaded by this app.</span></span>
        </label>
        <div class="actions">
          <button class="button primary" id="start" type="button">Start when ready</button>
        </div>
        <p id="intro-status" class="status" aria-live="polite"></p>
      </section>

      <section id="live" class="live" hidden>
        <div class="live-label">Keep going comfortably</div>
        <div id="elapsed" aria-live="off">0.0</div>
        <div class="meter" role="meter" aria-label="Device-relative voice level" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="meter-fill" id="meter-fill"></div><div class="meter-target" aria-hidden="true"></div>
        </div>
        <div class="meter-labels"><span>softer</span><span>steady zone</span><span>stronger</span></div>
        <p id="live-guidance" aria-live="polite"></p>
        <div class="actions" style="justify-content:center">
          <button class="button stop" id="stop" type="button">Stop this round</button>
        </div>
      </section>

      <section id="result" class="result" hidden>
        <div class="eyebrow">Round complete</div>
        <h2>Nice work. Take your time.</h2>
        <div class="stats">
          <div class="stat"><b id="result-duration">—</b><span>seconds</span></div>
          <div class="stat"><b id="result-target">—</b><span>time in your device-relative steady zone</span></div>
        </div>
        <div class="rating">
          <span>How did that round feel? Optional.</span>
          <div class="rating-row" role="group" aria-label="How the round felt">
            <button type="button" data-rating="1" aria-pressed="false">Comfortable</button>
            <button type="button" data-rating="2" aria-pressed="false">Okay</button>
            <button type="button" data-rating="3" aria-pressed="false">Effortful</button>
          </div>
        </div>
        <p id="status" class="status" aria-live="polite"></p>
        <div class="actions">
          <button class="button primary" id="save" type="button">Save this round</button>
          <button class="button secondary" id="next" type="button" hidden>Next round</button>
          <button class="button secondary" id="finish" type="button" hidden>Finish for today</button>
        </div>
      </section>
    </main>

    <aside>
      <section class="card side-card">
        <h2>How it works</h2>
        <ol class="steps">
          <li><span class="step-num">1</span><span>Tap start and take the breath you need.</span></li>
          <li><span class="step-num">2</span><span>Say “ah” steadily. The bar follows this device's microphone.</span></li>
          <li><span class="step-num">3</span><span>Stop yourself, rest, and continue only when ready.</span></li>
        </ol>
        <p class="fine">Voice level is device-relative feedback, not a clinical score or calibrated loudness measurement.</p>
      </section>
      <section class="card side-card">
        <h2>Recent rounds</h2>
        <p id="history-empty">Your completed rounds will appear here.</p>
        <div id="history" class="history"></div>
      </section>
    </aside>
  </div>
</div>

<script>
const TARGET_SECONDS = 10;
const THRESHOLD_DBFS = -35;
const VOICE_THRESHOLD_DBFS = -55;
const PROMPT_TEXT = 'Take a comfortable breath, then say ah steadily.';
const sessionKey = (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
const $ = id => document.getElementById(id);

let round = 1;
let running = false;
let audioContext = null;
let stream = null;
let analyser = null;
let frame = null;
let startedAt = 0;
let voicedDbSum = 0;
let peakDb = -160;
let analyzedCount = 0;
let voicedCount = 0;
let targetCount = 0;
let clientAttemptId = null;
let mediaSettings = {};
let sampleRateHz = 48_000;
let channelCount = 1;
let recorder = null;
let chunks = [];
let retainedBlob = null;
let retainedMime = null;
let retentionIssue = '';
let savedRoundCount = 0;
let saveInFlight = false;
let saveMayHaveReachedServer = false;
let sessionClosed = false;
let selectedRating = null;
let lastMetrics = null;

function show(name) {
  for (const id of ['intro', 'live', 'result']) $(id).hidden = id !== name;
}

function setStatus(text, isError = false) {
  $('status').textContent = text;
  $('status').className = isError ? 'status error' : 'status';
}

function updatePips() {
  document.querySelectorAll('.pip').forEach((pip, index) => {
    pip.className = 'pip';
    if (index < round - 1) pip.classList.add('done');
    else if (index === round - 1 && round <= 3) pip.classList.add('current');
  });
  $('round-number').textContent = String(Math.min(round, 3));
}

function bestRecorderMime() {
  if (!window.MediaRecorder) return null;
  if (typeof MediaRecorder.isTypeSupported !== 'function') return '';
  const choices = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm', 'audio/ogg;codecs=opus'];
  return choices.find(type => MediaRecorder.isTypeSupported(type)) || '';
}

function configureRetentionSupport() {
  if (bestRecorderMime() !== null) return;
  $('save-audio').checked = false;
  $('save-audio').disabled = true;
  $('retention-note').textContent = 'Recording retention is not supported in this browser. Practice metrics still work.';
}

function canonicalMime(type) {
  const plain = (type || '').split(';')[0].toLowerCase();
  return ['audio/aac', 'audio/mp4', 'audio/ogg', 'audio/wav', 'audio/webm', 'audio/x-wav'].includes(plain) ? plain : null;
}

function randomId() {
  return (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

function appliedBoolean(name) {
  return typeof mediaSettings[name] === 'boolean' ? mediaSettings[name] : null;
}

async function releaseAudioGraph() {
  if (stream) stream.getTracks().forEach(track => track.stop());
  if (audioContext && audioContext.state !== 'closed') {
    try { await audioContext.close(); } catch (error) { /* already closing */ }
  }
  stream = null;
  audioContext = null;
  analyser = null;
}

async function abortAttempt(message) {
  running = false;
  if (frame) cancelAnimationFrame(frame);
  if (recorder && recorder.state !== 'inactive') {
    try { recorder.stop(); } catch (error) { /* recorder never fully started */ }
  }
  recorder = null;
  await releaseAudioGraph();
  if (message) {
    show('intro');
    $('intro-status').textContent = message;
    $('intro-status').className = 'status error';
  }
}

async function drainRecorderWithFallback() {
  if (!recorder || recorder.state === 'inactive') return true;
  const recorderResult = new Promise(resolve => {
    recorder.addEventListener('stop', () => resolve(true), {once: true});
    recorder.addEventListener('error', () => resolve(false), {once: true});
    try { recorder.stop(); } catch (error) { resolve(false); }
  });
  const timeoutResult = new Promise(resolve => setTimeout(() => resolve(false), 1500));
  return Promise.race([recorderResult, timeoutResult]);
}

async function startAttempt() {
  if (running) return;
  $('intro-status').textContent = '';
  $('intro-status').className = 'status';
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    $('intro-status').textContent = 'Microphone access is unavailable here. Open this page in the Parker app or on localhost.';
    $('intro-status').className = 'status error';
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: {
      echoCancellation: false, noiseSuppression: false, autoGainControl: false
    }});
    const track = stream.getAudioTracks()[0];
    mediaSettings = track && track.getSettings ? track.getSettings() : {};
    if (track) {
      track.addEventListener('ended', () => {
        if (running) abortAttempt('The microphone disconnected. This round was not saved.');
      }, {once: true});
    }
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    await audioContext.resume();
    sampleRateHz = Number(mediaSettings.sampleRate || audioContext.sampleRate || 48_000);
    channelCount = Number(mediaSettings.channelCount || 1);
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = .18;
    source.connect(analyser);

    chunks = [];
    retainedBlob = null;
    retainedMime = null;
    retentionIssue = '';
    if ($('save-audio').checked) {
      const mime = bestRecorderMime();
      if (mime === null) {
        retentionIssue = 'Recording retention is not supported in this browser. Round metrics still work.';
      } else {
        try {
          recorder = mime ? new MediaRecorder(stream, {mimeType: mime}) : new MediaRecorder(stream);
          recorder.ondataavailable = event => { if (event.data && event.data.size) chunks.push(event.data); };
          recorder.start();
        } catch (error) {
          recorder = null;
          retentionIssue = 'Parker could not start the optional recording. Round metrics still work.';
        }
      }
    }

    running = true;
    clientAttemptId = randomId();
    startedAt = performance.now();
    voicedDbSum = 0;
    peakDb = -160;
    analyzedCount = 0;
    voicedCount = 0;
    targetCount = 0;
    $('elapsed').textContent = '0.0';
    $('meter-fill').style.width = '0%';
    $('live-guidance').textContent = '';
    show('live');
    monitor();
  } catch (error) {
    await abortAttempt('Parker could not open the microphone. Check microphone permission, then try again.');
  }
}

function monitor() {
  if (!running || !analyser) return;
  const samples = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(samples);
  let squareSum = 0;
  for (const value of samples) squareSum += value * value;
  const rms = Math.sqrt(squareSum / samples.length);
  const db = Math.max(-100, Math.min(0, 20 * Math.log10(Math.max(rms, 1e-8))));
  analyzedCount += 1;
  peakDb = Math.max(peakDb, db);
  if (db >= VOICE_THRESHOLD_DBFS) {
    voicedCount += 1;
    voicedDbSum += db;
    if (db >= THRESHOLD_DBFS) targetCount += 1;
  }

  const width = Math.max(0, Math.min(100, ((db + 70) / 70) * 100));
  $('meter-fill').style.width = `${width}%`;
  document.querySelector('.meter').setAttribute('aria-valuenow', String(Math.round(width)));
  const elapsed = (performance.now() - startedAt) / 1000;
  $('elapsed').textContent = elapsed.toFixed(1);
  $('live-guidance').textContent = elapsed >= TARGET_SECONDS ? 'You reached the guide. Continue or stop when you are ready.' : '';
  frame = requestAnimationFrame(monitor);
}

async function stopAttempt() {
  if (!running) return;
  running = false;
  cancelAnimationFrame(frame);
  const duration = Math.max(.1, (performance.now() - startedAt) / 1000);

  let recorderFinalized = true;
  try {
    if (recorder && recorder.state !== 'inactive') {
      recorderFinalized = await drainRecorderWithFallback();
    }
    if ($('save-audio').checked && !retentionIssue) {
      if (!recorderFinalized) {
        retentionIssue = 'The optional recording could not finish. Round metrics are still available.';
      } else if (!chunks.length) {
        retentionIssue = 'No optional recording data was produced. Round metrics are still available.';
      } else {
        retainedBlob = new Blob(chunks, {type: recorder.mimeType || chunks[0].type});
        retainedMime = canonicalMime(retainedBlob.type);
        if (!retainedMime) {
          retentionIssue = 'This browser produced an unsupported recording type. Round metrics are still available.';
          retainedBlob = null;
        } else if (retainedBlob.size > 2 * 1024 * 1024) {
          retentionIssue = 'The optional recording was too large to keep. Round metrics are still available.';
          retainedBlob = null;
          retainedMime = null;
        }
      }
    }
  } catch (error) {
    recorderFinalized = false;
    retentionIssue = 'The optional recording could not finish. Round metrics are still available.';
    retainedBlob = null;
    retainedMime = null;
  } finally {
    await releaseAudioGraph();
    recorder = null;
  }

  const averageDb = voicedCount ? voicedDbSum / voicedCount : -100;
  const measuredPeak = analyzedCount ? peakDb : -100;
  const relativeFraction = voicedCount ? targetCount / voicedCount : null;
  lastMetrics = {
    duration_seconds: Number(duration.toFixed(2)),
    average_dbfs: Number(averageDb.toFixed(2)),
    peak_dbfs: Number(measuredPeak.toFixed(2)),
    analyzed_sample_count: Math.max(1, analyzedCount),
    voiced_sample_count: voicedCount,
    in_target_sample_count: targetCount,
  };
  $('result-duration').textContent = duration.toFixed(1);
  $('result-target').textContent = relativeFraction === null ? '—' : `${Math.round(relativeFraction * 100)}%`;
  $('save').hidden = false;
  $('save').disabled = false;
  $('next').hidden = true;
  selectedRating = null;
  document.querySelectorAll('[data-rating]').forEach(button => {
    button.classList.remove('selected');
    button.setAttribute('aria-pressed', 'false');
  });
  setStatus(retentionIssue, Boolean(retentionIssue));
  show('result');
}

async function blobBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

async function saveAttempt() {
  if (!lastMetrics) return;
  $('save').disabled = true;
  setStatus('Saving this round on Parker…');
  saveInFlight = true;
  saveMayHaveReachedServer = true;
  try {
    const payload = {
    client_attempt_id: clientAttemptId,
    practice_session_key: sessionKey,
    sequence: round,
    exercise_key: 'sustained_ah',
    protocol_version: 'sustained-ah-v1',
    prompt_text: PROMPT_TEXT,
    target_seconds: TARGET_SECONDS,
    threshold_dbfs: THRESHOLD_DBFS,
    measurement_kind: 'device_relative_dbfs',
    measurement_algorithm_version: 'rms-frame-v1',
    sample_rate_hz: sampleRateHz,
    channel_count: channelCount,
    auto_gain_control: appliedBoolean('autoGainControl'),
    noise_suppression: appliedBoolean('noiseSuppression'),
    echo_cancellation: appliedBoolean('echoCancellation'),
    self_rating: selectedRating,
    ...lastMetrics,
  };
  if (retainedBlob && retainedMime && retainedBlob.size <= 2 * 1024 * 1024) {
    payload.audio_mime = retainedMime;
    payload.audio_base64 = await blobBase64(retainedBlob);
  }
    const response = await fetch('/parker/practice/attempts', {
      method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    savedRoundCount = Math.max(savedRoundCount, round);
    if (saved.audio_saved) {
      setStatus('Round saved, including your local recording.');
    } else if (retentionIssue) {
      setStatus(`Round metrics saved. ${retentionIssue}`, true);
    } else {
      setStatus('Round saved. No audio recording was kept.');
    }
    $('save').hidden = true;
    $('next').hidden = round >= 3;
    $('finish').hidden = false;
    await loadHistory();
  } catch (error) {
    setStatus('This round was not saved. You can try again without repeating it.', true);
    $('save').disabled = false;
  } finally {
    saveInFlight = false;
  }
}

function showFinishedPractice() {
  round = 4;
  updatePips();
  show('intro');
  $('intro').querySelector('.eyebrow').textContent = 'Practice complete';
  $('intro').querySelector('h1').textContent = savedRoundCount === 1 ? 'One round, done.' : `${savedRoundCount} rounds, done.`;
  $('intro').querySelector('.lead').textContent = 'That is enough for today. Parker saved your practice on this device.';
  $('intro').querySelector('.pace-note').hidden = true;
  $('intro').querySelector('.consent').hidden = true;
  $('intro').querySelector('.actions').hidden = true;
}

async function finishPractice() {
  try {
    const response = await fetch(`/parker/practice/sessions/${encodeURIComponent(sessionKey)}/complete`, {method: 'POST'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    sessionClosed = true;
    showFinishedPractice();
  } catch (error) {
    setStatus('Parker could not finish this practice yet. Please try again.', true);
  }
}

function nextRound() {
  if (round >= 3) return;
  round += 1;
  updatePips();
  clientAttemptId = null;
  retainedBlob = null;
  retainedMime = null;
  retentionIssue = '';
  lastMetrics = null;
  $('finish').hidden = true;
  show('intro');
  $('start').focus();
}

async function loadHistory() {
  try {
    const response = await fetch('/parker/practice/attempts?limit=5', {cache: 'no-store'});
    if (!response.ok) return;
    const data = await response.json();
    const history = $('history');
    history.textContent = '';
    $('history-empty').hidden = data.attempts.length > 0;
    for (const attempt of data.attempts) {
      const row = document.createElement('div');
      row.className = 'history-row';
      const when = document.createElement('span');
      when.textContent = new Date(attempt.completed_at.endsWith('Z') ? attempt.completed_at : attempt.completed_at + 'Z').toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
      const result = document.createElement('b');
      result.textContent = `${attempt.duration_seconds.toFixed(1)}s`;
      row.append(when, result);
      history.appendChild(row);
    }
  } catch (error) {
    // Progress is helpful but never blocks a practice round.
  }
}

document.querySelectorAll('[data-rating]').forEach(button => {
  button.addEventListener('click', () => {
    selectedRating = Number(button.dataset.rating);
    document.querySelectorAll('[data-rating]').forEach(other => {
      const selected = other === button;
      other.classList.toggle('selected', selected);
      other.setAttribute('aria-pressed', String(selected));
    });
  });
});

function abandonPracticeOnExit() {
  if ((savedRoundCount > 0 || saveInFlight || saveMayHaveReachedServer) && !sessionClosed) {
    const abandonUrl = `/parker/practice/sessions/${encodeURIComponent(sessionKey)}/abandon`;
    const beaconQueued = typeof navigator.sendBeacon === 'function' && navigator.sendBeacon(abandonUrl);
    if (!beaconQueued) {
      fetch(abandonUrl, {method: 'POST', keepalive: true}).catch(() => {});
    }
    sessionClosed = true;
  }
  abortAttempt('');
}

window.addEventListener('pagehide', abandonPracticeOnExit);
document.addEventListener('visibilitychange', () => {
  if (document.hidden && running) abortAttempt('The practice paused when this page was hidden. Start again when ready.');
});

$('start').addEventListener('click', startAttempt);
$('stop').addEventListener('click', stopAttempt);
$('save').addEventListener('click', saveAttempt);
$('next').addEventListener('click', nextRound);
$('finish').addEventListener('click', finishPractice);
configureRetentionSupport();
updatePips();
loadHistory();
</script>
</body>
</html>
"""
