/*
 * Executable browser-lifecycle tests for the REAL Converse page script.
 *
 * Usage: node converse_page.spec.js <path-to-extracted-inline-script>
 * (tests/test_expression_state.py extracts the inline conversation script
 * from converse_ui.py and passes it in.)
 *
 * These pin the interleavings the independent review (2026-09-01) proved
 * broken by reproduction, against the page's actual code — not source
 * strings: guard TTS vs Stop and line close, drained-vs-response truth
 * under network jitter, a socket that opens after Stop, page-hide
 * resource teardown, and repeated session cycles.
 */
'use strict';

const assert = require('assert');
const { createEnv } = require('./converse_page_env');

const pageScript = process.argv[2];
if (!pageScript) {
  process.stderr.write('usage: node converse_page.spec.js <inline-script.js>\n');
  process.exit(2);
}

const results = [];
async function test(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (err) {
    results.push({ name, ok: false, error: (err && err.stack) || String(err) });
  }
}

async function bootedEnv() {
  const env = createEnv();
  await env.boot(pageScript);
  return env;
}

function phase(env) {
  return env.context.ParkerPresence.controller.getState().phase;
}

async function liveSession(env) {
  await env.context.startLive();
  await env.flush();
  const ws = env.sockets[env.sockets.length - 1];
  ws.open();
  return ws;
}

(async () => {
  await test('boot reaches idle with Live as the primary control', async () => {
    const env = await bootedEnv();
    assert.strictEqual(env.document.body.dataset.state, 'idle');
    assert.ok(env.document.body.classList.contains('live-primary'));
    assert.strictEqual(phase(env), 'idle');
  });

  await test('a socket that finishes opening after Stop cannot restore live state', async () => {
    const env = await bootedEnv();
    await env.context.startLive();
    await env.flush();
    const ws = env.sockets[0];
    env.context.stopParker(); // Stop lands while the socket is still CONNECTING
    assert.strictEqual(env.document.body.dataset.state, 'stopped');
    ws.open(); // the late open on the dead line
    assert.strictEqual(env.document.body.dataset.state, 'stopped');
    assert.strictEqual(phase(env), 'stopped');
    assert.ok(ws.closed, 'the dead socket must have been closed');
  });

  await test('guard redirect speech dies with Stop and stays dead', async () => {
    const env = await bootedEnv();
    const ws = await liveSession(env);
    ws.message({ type: 'user_transcript', text: 'should I change my dose' });
    ws.message({ type: 'clear' });
    ws.message({ type: 'guard_redirect', text: 'That one is for your doctor or family.' });
    assert.strictEqual(env.utterances.length, 1, 'the redirect must be spoken');
    const u = env.utterances[0];
    u.onstart();
    assert.strictEqual(phase(env), 'talking'); // audible speech IS talking
    const cancelsBefore = env.ttsCancels;
    env.context.stopParker();
    assert.ok(env.ttsCancels > cancelsBefore, 'Stop must cancel browser TTS');
    assert.ok(u.cancelled, 'the redirect utterance itself is cancelled');
    assert.strictEqual(env.document.body.dataset.state, 'stopped');
    if (u.onend) u.onend(); // the cancelled utterance still settles async
    assert.strictEqual(phase(env), 'stopped'); // ...and changes nothing
  });

  await test('guard redirect speech dies when the line closes', async () => {
    const env = await bootedEnv();
    const ws = await liveSession(env);
    ws.message({ type: 'user_transcript', text: 'should I change my dose' });
    ws.message({ type: 'guard_redirect', text: 'That one is for your doctor or family.' });
    const u = env.utterances[0];
    u.onstart();
    const cancelsBefore = env.ttsCancels;
    ws.dropped(); // abrupt close, no goodbye handshake
    assert.ok(env.ttsCancels > cancelsBefore, 'line close must cancel browser TTS');
    assert.strictEqual(env.document.body.dataset.state, 'error'); // not his Stop
    if (u.onend) u.onend();
    assert.strictEqual(phase(env), 'error');
  });

  await test('an inter-chunk gap never claims listening while the response is active', async () => {
    const env = await bootedEnv();
    const ws = await liveSession(env);
    ws.message({ type: 'user_transcript', text: 'what is the weather' });
    ws.message({ type: 'audio', data: env.pcmBase64(12000) }); // 0.5 s
    assert.strictEqual(phase(env), 'talking');
    env.advance(120); // playback watcher sees the chunk playing
    env.advance(700); // the local queue fully drains — but the response is open
    assert.strictEqual(phase(env), 'talking', 'a queue gap alone must not mean listening');
    ws.message({ type: 'response_state', status: 'done' });
    assert.strictEqual(phase(env), 'listening'); // done + drained → truthfully listening
  });

  await test('response done while audio still plays waits for the real drain', async () => {
    const env = await bootedEnv();
    const ws = await liveSession(env);
    ws.message({ type: 'user_transcript', text: 'what is the weather' });
    ws.message({ type: 'audio', data: env.pcmBase64(12000) });
    env.advance(120);
    ws.message({ type: 'response_state', status: 'done' }); // done first
    assert.strictEqual(phase(env), 'talking', 'audio is still audibly playing');
    env.advance(700); // now the queue actually drains
    assert.strictEqual(phase(env), 'listening');
  });

  await test('guard speech holds the talking claim until it settles', async () => {
    const env = await bootedEnv();
    const ws = await liveSession(env);
    ws.message({ type: 'user_transcript', text: 'should I change my dose' });
    ws.message({ type: 'guard_redirect', text: 'That one is for your doctor or family.' });
    ws.message({ type: 'response_state', status: 'done' }); // cancelled response closed
    const u = env.utterances[0];
    u.onstart();
    assert.strictEqual(phase(env), 'talking');
    env.advance(500);
    assert.strictEqual(phase(env), 'talking', 'guard TTS is still audible');
    u.onend(); // the redirect finished being heard
    assert.strictEqual(phase(env), 'listening');
  });

  await test('page hide releases microphone, audio, sockets, TTS, timers, and the scene', async () => {
    const env = await bootedEnv();
    const ws = await liveSession(env);
    ws.message({ type: 'audio', data: env.pcmBase64(2400) });
    const scene = { disposed: false, dispose() { this.disposed = true; } };
    env.context.ParkerPresence.scene = scene;
    const cancelsBefore = env.ttsCancels;
    env.firePagehide();
    for (const stream of env.streams) assert.ok(stream.track.stopped, 'mic track released');
    for (const ctx of env.audioContexts) assert.ok(ctx.closed, 'audio context closed');
    assert.ok(ws.closed, 'live socket closed');
    assert.ok(ws.sent.some((f) => f.type === 'end'), 'line told to end');
    assert.ok(env.ttsCancels > cancelsBefore, 'browser TTS cancelled');
    assert.strictEqual(env.intervalCount(), 0, 'no timer may survive page hide');
    assert.ok(scene.disposed, 'the GL scene is disposed');
    assert.ok(
      env.beacons.some((b) => b.url.includes('/end')),
      'the end beacon still goes out'
    );
    env.firePagehide(); // idempotent
    assert.strictEqual(env.reloads, 0);
    env.firePageshow(true); // a BFCache restore of a torn-down page
    assert.strictEqual(env.reloads, 1, 'restore must reload for a clean boot');
  });

  await test('repeated live start/stop cycles leak nothing', async () => {
    const env = await bootedEnv();
    for (let i = 0; i < 3; i++) {
      const ws = await liveSession(env);
      ws.message({ type: 'audio', data: env.pcmBase64(2400) });
      env.advance(240);
      env.context.stopParker();
      assert.ok(ws.closed, `cycle ${i}: socket closed`);
      assert.strictEqual(env.intervalCount(), 1, `cycle ${i}: only the truth heartbeat remains`);
    }
    for (const stream of env.streams) assert.ok(stream.track.stopped, 'every mic released');
    assert.strictEqual(env.streams.length, 3);
  });

  await test('semantic transitions stream to the bridge and flush as receipts', async () => {
    const env = await bootedEnv();
    const ws = await liveSession(env);
    ws.message({ type: 'user_transcript', text: 'hello there' });
    const frames = ws.sent.filter((f) => f.type === 'expression');
    assert.ok(frames.length >= 2, 'transitions must stream over the live socket');
    const thinking = frames.find((f) => f.to === 'thinking');
    assert.ok(thinking, 'the thinking transition is reported');
    assert.strictEqual(thinking.reason, 'user_transcript');
    assert.strictEqual(thinking.from, 'listening');
    env.context.stopParker();
    const receipt = env.beacons.find((b) => b.url.includes('/receipts'));
    assert.ok(receipt, 'stopping flushes the local receipt buffer');
    const parsed = JSON.parse(receipt.body);
    assert.ok(Array.isArray(parsed.expression) && parsed.expression.length >= 3);
    const last = parsed.expression[parsed.expression.length - 1];
    assert.strictEqual(last.to, 'stopped');
  });

  const failed = results.filter((r) => !r.ok);
  for (const r of results) {
    process.stdout.write((r.ok ? 'ok  ' : 'FAIL') + '  ' + r.name + (r.ok ? '' : '\n' + r.error) + '\n');
  }
  process.stdout.write('\n' + (results.length - failed.length) + '/' + results.length + ' passed\n');
  process.exit(failed.length ? 1 : 0);
})();
