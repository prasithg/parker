/*
 * Executable lifecycle tests for the REAL companion page script
 * (companion_ui.py — the virtual Reachy embodiment: power + CC only).
 *
 * Usage: node companion_page.spec.js <path-to-extracted-inline-script>
 *
 * The companion owns the live full-duplex lane, so every interleaving the
 * independent review proved broken (guard TTS vs off/close, drain-vs-
 * response truth, stale socket opens, page-hide teardown) is pinned here
 * against the page's actual code — plus the take-2 contracts: real power
 * semantics, persisted settings, CC captions, and spoken-confirmation
 * cards that never ask him to tap anything.
 */
'use strict';

const assert = require('assert');
const { createEnv } = require('./converse_page_env');

const pageScript = process.argv[2];
if (!pageScript) {
  process.stderr.write('usage: node companion_page.spec.js <inline-script.js>\n');
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

async function bootedEnv(settings) {
  const env = createEnv();
  if (settings) Object.assign(env.settings, settings);
  await env.boot(pageScript);
  await env.flush();
  return env;
}

function phase(env) {
  return env.context.ParkerPresence.controller.getState().phase;
}
function power(env) {
  return env.document.body.dataset.power;
}
function card(env) {
  const el = env.element('card');
  return el.hidden ? null : { kind: el.className, text: el.textContent };
}
function click(env, id) {
  const el = env.element(id);
  (el._handlers.click || []).forEach((fn) => fn({}));
}

async function poweredOn(env) {
  await env.context.powerOn();
  await env.flush();
  const ws = env.sockets[env.sockets.length - 1];
  ws.open();
  return ws;
}

(async () => {
  await test('boots OFF: nothing listens until someone turns Parker on', async () => {
    const env = await bootedEnv();
    assert.strictEqual(power(env), 'off');
    assert.strictEqual(env.streams.length, 0, 'no microphone was opened');
    assert.strictEqual(env.sockets.length, 0, 'no live socket was opened');
    assert.strictEqual(phase(env), 'offline');
    assert.ok(/off/i.test(env.element('sr-status').textContent));
  });

  await test('persisted power restores across a reload', async () => {
    const env = await bootedEnv({ power_on: true });
    assert.ok(env.sockets.length >= 1, 'the line reopens on boot');
    env.sockets[0].open();
    assert.strictEqual(power(env), 'on');
    assert.strictEqual(phase(env), 'listening');
  });

  await test('power off releases everything and persists', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    assert.strictEqual(power(env), 'on');
    assert.ok(env.streams.length === 1);
    env.context.powerOff();
    assert.ok(env.streams[0].track.stopped, 'microphone released');
    assert.ok(ws.closed, 'socket closed');
    assert.ok(ws.sent.some((f) => f.type === 'end'));
    assert.strictEqual(power(env), 'off');
    assert.strictEqual(phase(env), 'offline');
    assert.strictEqual(env.settings.power_on, false, 'off is persisted');
    assert.strictEqual(env.intervalCount(), 1, 'only the truth heartbeat remains');
  });

  await test('a socket that opens after power-off cannot restore the line', async () => {
    const env = await bootedEnv();
    await env.context.powerOn();
    await env.flush();
    const ws = env.sockets[0];
    env.context.powerOff(); // off while still CONNECTING
    ws.open();
    assert.strictEqual(power(env), 'off');
    assert.strictEqual(phase(env), 'offline');
  });

  await test('guard redirect speech dies with power off and stays dead', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.message({ type: 'user_transcript', text: 'should I change my dose' });
    ws.message({ type: 'guard_redirect', text: 'That one is for your doctor or family.' });
    assert.ok(card(env) && /doctor/.test(card(env).text), 'the redirect is shown');
    const u = env.utterances[0];
    u.onstart();
    assert.strictEqual(phase(env), 'talking');
    const cancelsBefore = env.ttsCancels;
    env.context.powerOff();
    assert.ok(env.ttsCancels > cancelsBefore, 'power off cancels browser TTS');
    if (u.onend) u.onend();
    assert.strictEqual(phase(env), 'offline');
  });

  await test('an inter-chunk gap never claims listening while the response is active', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.message({ type: 'user_transcript', text: 'what is the weather' });
    ws.message({ type: 'audio', data: env.pcmBase64(12000) });
    assert.strictEqual(phase(env), 'talking');
    env.advance(120);
    env.advance(700);
    assert.strictEqual(phase(env), 'talking', 'a queue gap alone must not mean listening');
    ws.message({ type: 'response_state', status: 'done' });
    assert.strictEqual(phase(env), 'listening');
  });

  await test('a staged offer is a spoken question — no tapping, ever', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.message({ type: 'user_transcript', text: 'remind me to take my meds at three' });
    ws.message({
      type: 'proposal_staged',
      label: 'a 3 PM meds reminder',
      readback: 'a reminder about “take meds at 3 PM”',
    });
    const staged = card(env);
    assert.ok(staged && staged.kind === 'staged');
    assert.ok(/say/.test(staged.text) && /yes/.test(staged.text), staged.text);
    assert.ok(!/tap|button|press|touch/i.test(staged.text), 'never asks him to tap');
    const s = env.context.ParkerPresence.controller.getState();
    assert.strictEqual(s.attention, 'confirmation');
    assert.strictEqual(s.action, 'staged');
  });

  await test('the real outcome frame drives the executed/failed/cancelled cards', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.message({ type: 'user_transcript', text: 'remind me' });
    ws.message({ type: 'proposal_staged', label: 'a reminder', readback: 'a reminder' });
    ws.message({ type: 'action_result', status: 'executed', label: 'a reminder' });
    let c = card(env);
    assert.ok(c && c.kind === 'executed' && /done/i.test(c.text), JSON.stringify(c));
    assert.strictEqual(env.context.ParkerPresence.controller.getState().action, 'executed');

    ws.message({ type: 'proposal_staged', label: 'a message', readback: 'a message' });
    ws.message({ type: 'action_result', status: 'failed', label: 'a message' });
    c = card(env);
    assert.ok(c && c.kind === 'failed', JSON.stringify(c));
    assert.ok(/family review/i.test(c.text), 'failure points at the review page');
    assert.ok(!/done/i.test(c.text));

    ws.message({ type: 'proposal_staged', label: 'x', readback: 'x' });
    ws.message({ type: 'action_result', status: 'cancelled', label: 'x' });
    c = card(env);
    assert.ok(c && c.kind === 'cancelled', JSON.stringify(c));

    ws.message({ type: 'proposal_staged', label: 'y', readback: 'y' });
    ws.message({ type: 'action_result', status: 'expired', label: 'y' });
    assert.strictEqual(card(env), null, 'an expired offer just lapses');
    assert.strictEqual(env.context.ParkerPresence.controller.getState().attention, 'none');
  });

  await test('CC captions appear only when CC is on, and persist the choice', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.message({ type: 'user_transcript', text: 'hello parker' });
    assert.ok(env.element('cc-him').hidden, 'captions stay hidden with CC off');
    click(env, 'cc-toggle');
    assert.strictEqual(env.settings.cc_on, true, 'CC choice is persisted');
    ws.message({ type: 'user_transcript', text: 'how are you' });
    assert.ok(!env.element('cc-him').hidden);
    assert.strictEqual(env.element('cc-him').textContent, 'how are you');
    ws.message({ type: 'assistant_transcript_delta', text: 'I’m here' });
    ws.message({ type: 'assistant_transcript_delta', text: ' with you.' });
    assert.strictEqual(env.element('cc-parker').textContent, 'I’m here with you.');
  });

  await test('a dropped line retries once, honestly', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.dropped();
    assert.strictEqual(power(env), 'error');
    const c = card(env);
    assert.ok(c && /reconnect/i.test(c.text), JSON.stringify(c));
    const socketsBefore = env.sockets.length;
    env.advance(2600);
    await env.flush();
    assert.strictEqual(env.sockets.length, socketsBefore + 1, 'one quiet retry');
    env.sockets[env.sockets.length - 1].open();
    assert.strictEqual(power(env), 'on');
  });

  await test('page hide releases microphone, audio, socket, TTS, timers, and the scene', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.message({ type: 'audio', data: env.pcmBase64(2400) });
    const scene = { disposed: false, dispose() { this.disposed = true; } };
    env.context.ParkerPresence.scene = scene;
    env.firePagehide();
    for (const stream of env.streams) assert.ok(stream.track.stopped);
    for (const ctx of env.audioContexts) assert.ok(ctx.closed);
    assert.ok(ws.closed && ws.sent.some((f) => f.type === 'end'));
    assert.strictEqual(env.intervalCount(), 0);
    assert.ok(scene.disposed);
    assert.ok(env.beacons.some((b) => b.url.includes('/end')));
    env.firePageshow(true);
    assert.strictEqual(env.reloads, 1, 'a BFCache restore reloads clean');
  });

  await test('semantic transitions stream to the bridge and flush as receipts', async () => {
    const env = await bootedEnv();
    const ws = await poweredOn(env);
    ws.message({ type: 'user_transcript', text: 'hello there' });
    const frames = ws.sent.filter((f) => f.type === 'expression');
    const thinking = frames.find((f) => f.to === 'thinking');
    assert.ok(thinking && thinking.reason === 'user_transcript');
    env.context.powerOff();
    const receipt = env.beacons.find((b) => b.url.includes('/receipts'));
    assert.ok(receipt, 'power off flushes the receipt buffer');
    const parsed = JSON.parse(receipt.body);
    assert.ok(Array.isArray(parsed.expression) && parsed.expression.length >= 2);
  });

  await test('Escape is the keyboard power-off', async () => {
    const env = await bootedEnv();
    await poweredOn(env);
    env.keydown.forEach((fn) => fn({ key: 'Escape' }));
    assert.strictEqual(power(env), 'off');
    assert.ok(env.streams[0].track.stopped);
  });

  const failed = results.filter((r) => !r.ok);
  for (const r of results) {
    process.stdout.write((r.ok ? 'ok  ' : 'FAIL') + '  ' + r.name + (r.ok ? '' : '\n' + r.error) + '\n');
  }
  process.stdout.write('\n' + (results.length - failed.length) + '/' + results.length + ' passed\n');
  process.exit(failed.length ? 1 : 0);
})();
