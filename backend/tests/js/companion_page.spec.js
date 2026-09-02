/*
 * Executable lifecycle tests for the REAL companion page script
 * (companion_ui.py — the virtual Reachy embodiment with local wake).
 *
 * Usage: node companion_page.spec.js <path-to-extracted-inline-script>
 *
 * Pins the take-2 + wake-word contracts against the page's actual code:
 * real power semantics with DORMANCY (power on = local wake lane only,
 * no cloud socket, lifeless scene until "Hey Parker"), the wake pop,
 * return-to-dormancy after the gentle wind-down, spoken-confirmation
 * cards that never ask him to tap, CC, and every interleaving the
 * independent review proved broken on the live lane.
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
function wakeSockets(env) {
  return env.sockets.filter((s) => s.url.includes('/converse/wake'));
}
function liveSockets(env) {
  return env.sockets.filter((s) => s.url.includes('/converse/realtime'));
}

async function poweredDormant(env) {
  await env.context.powerOn();
  await env.flush();
  const ws = wakeSockets(env)[wakeSockets(env).length - 1];
  ws.open();
  return ws;
}

async function poweredActive(env) {
  const wakeWs = await poweredDormant(env);
  wakeWs.message({ type: 'wake', heard: 'hey parker', matched: 'hey parker' });
  await env.flush();
  const ws = liveSockets(env)[liveSockets(env).length - 1];
  ws.open();
  return ws;
}

(async () => {
  await test('boots OFF: nothing listens until someone turns Parker on', async () => {
    const env = await bootedEnv();
    assert.strictEqual(power(env), 'off');
    assert.strictEqual(env.streams.length, 0, 'no microphone was opened');
    assert.strictEqual(env.sockets.length, 0, 'no socket of any kind');
    assert.strictEqual(phase(env), 'offline');
    assert.ok(/off/i.test(env.element('sr-status').textContent));
  });

  await test('power on means DORMANT: local wake lane only, no cloud line', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    assert.strictEqual(power(env), 'dormant');
    assert.strictEqual(phase(env), 'dormant');
    assert.strictEqual(env.streams.length, 1, 'the mic is held locally');
    assert.strictEqual(liveSockets(env).length, 0, 'NO cloud socket while dormant');
    assert.ok(/hey parker/i.test(env.element('sr-status').textContent));
    // Mic frames route ONLY to the local wake lane, resampled to 16 kHz.
    assert.ok(env.micFrame(0.2));
    const frames = wakeWs.sent.filter((f) => f.type === 'audio');
    assert.strictEqual(frames.length, 1);
    // 4096 samples @48k -> ~1365 @16k -> ~2730 bytes -> ~3640 base64 chars
    assert.ok(frames[0].data.length > 3000 && frames[0].data.length < 4200,
      'frames are 16 kHz-sized, not 24 kHz');
    assert.strictEqual(phase(env), 'dormant', 'room sound never animates a dormant scene');
  });

  await test('"Hey Parker" pops the scene awake and opens the line', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    wakeWs.message({ type: 'wake', heard: 'hey parker', matched: 'hey parker' });
    assert.strictEqual(phase(env), 'connecting', 'the pop begins immediately');
    await env.flush();
    assert.ok(wakeWs.closed, 'the wake lane closes on detection');
    const live = liveSockets(env)[0];
    assert.ok(live, 'the realtime line opens');
    live.open();
    assert.strictEqual(phase(env), 'listening');
    assert.strictEqual(power(env), 'on');
    // The wake transition is in the receipts for session review.
    const receiptsAfterOff = () => {
      env.context.powerOff();
      const receipt = env.beacons.find((b) => b.url.includes('/receipts'));
      return JSON.parse(receipt.body).expression;
    };
    const transitions = receiptsAfterOff();
    const popped = transitions.find((t) => t.from === 'dormant' && t.to === 'connecting');
    assert.ok(popped && popped.reason === 'wake_detected', JSON.stringify(transitions));
  });

  await test('the gentle wind-down returns to dormancy, wake re-armed', async () => {
    const env = await bootedEnv();
    const live = await poweredActive(env);
    const wakeCountBefore = wakeSockets(env).length;
    live.message({ type: 'closing' });
    env.advance(400); // the drain timer fires
    assert.ok(live.closed, 'the cloud line closed');
    assert.strictEqual(power(env), 'dormant');
    assert.strictEqual(phase(env), 'dormant');
    assert.strictEqual(wakeSockets(env).length, wakeCountBefore + 1, 'wake re-armed');
    assert.ok(!env.streams[0].track.stopped, 'the mic stays held — power is still on');
    // …and a second "hey parker" works: the full cycle repeats.
    const wakeWs = wakeSockets(env)[wakeSockets(env).length - 1];
    wakeWs.open();
    wakeWs.message({ type: 'wake', heard: 'hey parker', matched: 'hey parker' });
    await env.flush();
    assert.strictEqual(liveSockets(env).length, 2, 'a fresh line per wake');
  });

  await test('persisted power restores to DORMANT, not to a live line', async () => {
    const env = await bootedEnv({ power_on: true });
    await env.flush();
    assert.ok(wakeSockets(env).length >= 1, 'wake listening re-arms on boot');
    assert.strictEqual(liveSockets(env).length, 0, 'no cloud line without a wake');
    assert.strictEqual(power(env), 'dormant');
  });

  await test('power off from dormancy releases the mic and persists', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    env.context.powerOff();
    assert.ok(env.streams[0].track.stopped, 'microphone released');
    assert.ok(wakeWs.closed, 'wake lane closed');
    for (const ctx of env.audioContexts) assert.ok(ctx.closed, 'audio contexts closed');
    assert.strictEqual(power(env), 'off');
    assert.strictEqual(phase(env), 'offline');
    assert.strictEqual(env.settings.power_on, false, 'off is persisted');
  });

  await test('a wake frame after power-off cannot wake anything', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    env.context.powerOff();
    wakeWs.message({ type: 'wake', heard: 'hey parker', matched: 'hey parker' });
    await env.flush();
    assert.strictEqual(liveSockets(env).length, 0);
    assert.strictEqual(power(env), 'off');
  });

  await test('a dropped wake lane retries once, then is honest', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    wakeWs.dropped();
    env.advance(1600);
    const second = wakeSockets(env)[wakeSockets(env).length - 1];
    assert.notStrictEqual(second, wakeWs, 'one quiet retry');
    second.open();
    assert.strictEqual(power(env), 'dormant');
    second.dropped();
    env.advance(1600);
    assert.strictEqual(power(env), 'error');
    assert.ok(/wake listening/i.test(card(env).text), JSON.stringify(card(env)));
  });

  await test('a missing local model falls back to straight-active, honestly', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    wakeWs.message({ type: 'unavailable', text: 'Wake listening needs the local voice model.' });
    await env.flush();
    assert.ok(card(env) && /local voice model/i.test(card(env).text));
    const live = liveSockets(env)[0];
    assert.ok(live, 'Parker still works — take-2 behavior');
    live.open();
    assert.strictEqual(power(env), 'on');
    assert.strictEqual(phase(env), 'listening', 'the scene is truthfully live, not dormant');
  });

  await test('guard redirect speech dies with power off and stays dead', async () => {
    const env = await bootedEnv();
    const ws = await poweredActive(env);
    ws.message({ type: 'user_transcript', text: 'should I change my dose' });
    ws.message({ type: 'guard_redirect', text: 'That one is for your doctor or family.' });
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
    const ws = await poweredActive(env);
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
    const ws = await poweredActive(env);
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
    ws.message({ type: 'action_result', status: 'executed', label: 'a 3 PM meds reminder' });
    const done = card(env);
    assert.ok(done && done.kind === 'executed' && /done/i.test(done.text));
  });

  await test('CC captions appear only when CC is on, and persist the choice', async () => {
    const env = await bootedEnv();
    const ws = await poweredActive(env);
    ws.message({ type: 'user_transcript', text: 'hello parker' });
    assert.ok(env.element('cc-him').hidden, 'captions stay hidden with CC off');
    click(env, 'cc-toggle');
    assert.strictEqual(env.settings.cc_on, true, 'CC choice is persisted');
    ws.message({ type: 'user_transcript', text: 'how are you' });
    assert.ok(!env.element('cc-him').hidden);
    assert.strictEqual(env.element('cc-him').textContent, 'how are you');
  });

  await test('a dropped ACTIVE line retries the session, not dormancy', async () => {
    const env = await bootedEnv();
    const ws = await poweredActive(env);
    ws.dropped();
    assert.strictEqual(power(env), 'error');
    assert.ok(/reconnect/i.test(card(env).text));
    const liveBefore = liveSockets(env).length;
    env.advance(2600);
    await env.flush();
    assert.strictEqual(liveSockets(env).length, liveBefore + 1, 'one quiet retry');
    liveSockets(env)[liveSockets(env).length - 1].open();
    assert.strictEqual(power(env), 'on');
    assert.strictEqual(phase(env), 'listening', 'the retry re-enters truthfully');
  });

  await test('page hide releases microphone, audio, sockets, TTS, timers, and the scene', async () => {
    const env = await bootedEnv();
    const ws = await poweredActive(env);
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

  await test('Escape is the keyboard power-off, from dormant too', async () => {
    const env = await bootedEnv();
    await poweredDormant(env);
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
