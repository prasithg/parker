/*
 * A stubbed browser environment for executing the REAL Converse page
 * script (the inline conversation script from converse_ui.py) under Node.
 *
 * Nothing from the page is mocked or reimplemented — the page's own code
 * runs inside a vm context whose DOM/WebAudio/WebSocket/speechSynthesis
 * surfaces are controllable fakes, so tests can drive the interleavings
 * the independent review flagged (guard TTS vs Stop, drain vs response
 * lifecycle, stale socket opens, page-hide teardown) and observe what the
 * page actually did. Virtual timers make the interleavings deterministic.
 */
'use strict';

const vm = require('vm');
const fs = require('fs');
const path = require('path');

function createEnv() {
  const env = {
    now: 0, // virtual ms; performance.now(), timers, and audio time share it
    timers: new Map(),
    timerSeq: 1,
    sockets: [],
    utterances: [],
    ttsCancels: 0,
    beacons: [],
    fetches: [],
    pagehide: [],
    pageshow: [],
    keydown: [],
    audioContexts: [],
    processors: [],
    streams: [],
    reloads: 0,
    getUserMediaMode: 'grant', // or 'deny'
  };

  // ---------------------------------------------------------------- DOM
  function fakeClassList() {
    const set = new Set();
    return {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      contains: (c) => set.has(c),
    };
  }

  function fakeElement(id) {
    return {
      id,
      hidden: false,
      disabled: false,
      textContent: '',
      value: '',
      title: '',
      className: '',
      dataset: {},
      classList: fakeClassList(),
      children: [],
      _handlers: {},
      appendChild(child) { this.children.push(child); return child; },
      addEventListener(name, fn) { (this._handlers[name] ||= []).push(fn); },
      focus() {},
      remove() {},
      querySelector() { return fakeElement('anon'); },
      _attrs: {},
      setAttribute(name, value) { this._attrs[name] = String(value); },
      getAttribute(name) { return this._attrs[name] ?? null; },
    };
  }

  const elements = new Map();
  const documentObj = {
    body: fakeElement('body'),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, fakeElement(id));
      return elements.get(id);
    },
    querySelector() { return fakeElement('anon'); },
    addEventListener(name, fn) { if (name === 'keydown') env.keydown.push(fn); },
    createElement: (tag) => fakeElement(tag),
  };
  documentObj.body.dataset.state = 'starting';

  // ------------------------------------------------------------- timers
  function setTimer(fn, ms, repeat) {
    const id = env.timerSeq++;
    env.timers.set(id, { fn, at: env.now + (ms || 0), every: repeat ? (ms || 0) : null });
    return id;
  }

  env.advance = function advance(ms) {
    const target = env.now + ms;
    for (;;) {
      let nextId = null;
      let nextAt = Infinity;
      for (const [id, t] of env.timers) {
        if (t.at <= target && t.at < nextAt) { nextAt = t.at; nextId = id; }
      }
      if (nextId === null) break;
      const timer = env.timers.get(nextId);
      env.now = Math.max(env.now, timer.at);
      if (timer.every != null) timer.at = env.now + Math.max(1, timer.every);
      else env.timers.delete(nextId);
      try { timer.fn(); } catch (err) { /* page code must not crash the clock */ }
    }
    env.now = target;
  };

  env.intervalCount = () => {
    let n = 0;
    for (const t of env.timers.values()) if (t.every != null) n += 1;
    return n;
  };

  // -------------------------------------------------------------- audio
  class FakeAudioContext {
    constructor() {
      this.closed = false;
      this.state = 'running';
      this.sampleRate = 48000;
      this.destination = {};
      this.startedSources = [];
      env.audioContexts.push(this);
    }
    get currentTime() { return env.now / 1000; }
    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
    createOscillator() {
      return { frequency: { value: 0 }, type: '', connect() {}, start() {}, stop() {} };
    }
    createMediaStreamSource() { return { connect() {} }; }
    createScriptProcessor() {
      const proc = { onaudioprocess: null, connect() {}, disconnect() {} };
      env.processors.push(proc);
      return proc;
    }
    createBuffer(channels, length, rate) {
      const data = new Float32Array(length);
      return {
        length, sampleRate: rate, duration: length / rate,
        getChannelData: () => data,
      };
    }
    createBufferSource() {
      const ctx = this;
      const src = {
        buffer: null, onended: null, startedAt: null, stopped: false,
        connect() {},
        start(at) { src.startedAt = at; ctx.startedSources.push(src); },
        stop() { src.stopped = true; },
      };
      return src;
    }
    close() { this.closed = true; return Promise.resolve(); }
  }

  function fakeStream() {
    const track = { stopped: false, stop() { this.stopped = true; } };
    const stream = { track, getTracks: () => [track] };
    env.streams.push(stream);
    return stream;
  }

  // ---------------------------------------------------------- WebSocket
  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0; // CONNECTING
      this.sent = [];
      this.closed = false;
      this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
      env.sockets.push(this);
    }
    send(raw) {
      if (this.readyState !== 1) throw new Error('socket not open');
      this.sent.push(JSON.parse(raw));
    }
    close() { this.closed = true; }
    // test drivers
    open() { this.readyState = 1; if (this.onopen) this.onopen(); }
    message(obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); }
    dropped() { if (this.onclose) this.onclose(); }
  }

  // -------------------------------------------------------------- speech
  class FakeUtterance {
    constructor(text) {
      this.text = text;
      this.rate = 1;
      this.onstart = null; this.onend = null; this.onerror = null; this.onboundary = null;
      this.cancelled = false;
    }
  }
  const speechSynthesis = {
    speak(u) { env.utterances.push(u); },
    cancel() {
      env.ttsCancels += 1;
      for (const u of env.utterances) if (!u.done) u.cancelled = true;
    },
  };

  // ------------------------------------------------------------ network
  env.settings = { power_on: false, cc_on: false }; // the persisted store
  // The engine's power authority (companion_power.py), as the page sees it:
  // 'grant' issues owner credentials; 'elsewhere' refuses 409; 'fail' is a
  // 503 (write failed); 'unreachable' rejects the fetch. `offSave` controls
  // whether the OFF write lands ({saved:false} otherwise).
  env.powerMode = 'grant';
  env.offSave = true;
  env.powerGen = 0;
  env.powerClaims = [];
  env.powerReleases = [];
  function jsonResponse(body, status) {
    const code = status || 200;
    return { ok: code >= 200 && code < 300, status: code, json: async () => body, body: null };
  }
  const fetchImpl = (url, opts) => {
    env.fetches.push({ url, opts });
    if (String(url).includes('/companion/power')) {
      let body = {};
      try { body = JSON.parse(opts.body); } catch (err) {}
      if (body.on) {
        env.powerClaims.push(body);
        if (env.powerMode === 'unreachable') return Promise.reject(new Error('engine down'));
        if (env.powerMode === 'elsewhere') {
          return Promise.resolve(jsonResponse({ detail: { reason: 'elsewhere' } }, 409));
        }
        if (env.powerMode === 'fail') {
          return Promise.resolve(jsonResponse({ detail: { reason: 'not_saved' } }, 503));
        }
        env.powerGen += 1;
        env.settings.power_on = true;
        return Promise.resolve(jsonResponse({ power_on: true, owner: 'tok-' + env.powerGen, gen: env.powerGen }));
      }
      env.powerReleases.push(body);
      if (env.powerMode === 'unreachable') return Promise.reject(new Error('engine down'));
      env.powerGen += 1;
      if (env.offSave) env.settings.power_on = false;
      return Promise.resolve(jsonResponse({ power_on: false, saved: !!env.offSave }));
    }
    if (String(url).endsWith('/sessions') && opts && opts.method === 'POST') {
      return Promise.resolve(jsonResponse({
        session_id: 'sess-test', realtime_available: true, asr_ready: true,
      }));
    }
    if (String(url).includes('/companion/settings')) {
      if (opts && opts.method === 'POST') {
        try { Object.assign(env.settings, JSON.parse(opts.body)); } catch (err) {}
        return Promise.resolve(jsonResponse(env.settings));
      }
      return Promise.resolve(jsonResponse(Object.assign({}, env.settings)));
    }
    return Promise.resolve(jsonResponse({}));
  };

  class FakeBlob {
    constructor(parts) { this.body = parts.join(''); }
  }

  // ------------------------------------------------------------ sandbox
  const sandbox = {
    console,
    document: documentObj,
    navigator: {
      mediaDevices: {
        getUserMedia: () => (
          env.getUserMediaMode === 'grant'
            ? Promise.resolve(fakeStream())
            : Promise.reject(new Error('denied'))
        ),
      },
      sendBeacon: (url, blob) => {
        env.beacons.push({ url, body: blob && blob.body !== undefined ? blob.body : String(blob) });
        return true;
      },
    },
    location: {
      protocol: 'http:', host: 'localhost:8000',
      reload: () => { env.reloads += 1; },
    },
    performance: { now: () => env.now },
    setTimeout: (fn, ms) => setTimer(fn, ms, false),
    clearTimeout: (id) => { env.timers.delete(id); },
    setInterval: (fn, ms) => setTimer(fn, ms, true),
    clearInterval: (id) => { env.timers.delete(id); },
    WebSocket: FakeWebSocket,
    AudioContext: FakeAudioContext,
    SpeechSynthesisUtterance: FakeUtterance,
    speechSynthesis,
    fetch: fetchImpl,
    Blob: FakeBlob,
    AbortController,
    TextDecoder,
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
    atob: (s) => Buffer.from(s, 'base64').toString('binary'),
    matchMedia: () => ({ matches: false }),
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.window.addEventListener = (name, fn) => {
    if (name === 'pagehide') env.pagehide.push(fn);
    if (name === 'pageshow') env.pageshow.push(fn);
  };
  sandbox.window.speechSynthesis = speechSynthesis;

  const context = vm.createContext(sandbox);

  env.context = context;
  env.document = documentObj;
  env.element = (id) => documentObj.getElementById(id);
  env.firePagehide = () => env.pagehide.forEach((fn) => fn({}));
  env.firePageshow = (persisted) => env.pageshow.forEach((fn) => fn({ persisted: !!persisted }));
  env.flush = () => new Promise((resolve) => setImmediate(() => setImmediate(resolve)));
  env.pcmBase64 = (samples) => Buffer.alloc(samples * 2).toString('base64');
  env.micFrame = (level) => {
    const proc = env.processors[env.processors.length - 1];
    if (!proc || !proc.onaudioprocess) return false;
    const data = new Float32Array(4096).fill(level == null ? 0.2 : level);
    proc.onaudioprocess({ inputBuffer: { getChannelData: () => data } });
    return true;
  };

  env.boot = function boot(pageScriptPath) {
    const expressionSource = fs.readFileSync(
      path.join(__dirname, '..', '..', 'app', 'parker', 'static', 'converse', 'expression.js'),
      'utf8'
    );
    vm.runInContext(expressionSource, context, { filename: 'expression.js' });
    const pageSource = fs.readFileSync(pageScriptPath, 'utf8');
    vm.runInContext(pageSource, context, { filename: 'converse-page.js' });
    return env.flush(); // let createSession() settle
  };

  return env;
}

module.exports = { createEnv };
