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
  // Two live regions (polite status / assertive alert); one visible at a time.
  for (const id of ['card', 'alert']) {
    const el = env.element(id);
    if (!el.hidden) return { kind: el.className, text: el.textContent, region: id };
  }
  return null;
}
function query(url) {
  const q = url.indexOf('?');
  return q < 0 ? {} : Object.fromEntries(new URLSearchParams(url.slice(q + 1)));
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
async function poweredOff(env) {
  env.context.powerOff();
  await env.flush();
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
    wakeWs.message({ type: 'wake', heard: 'hey parker', matched: 'hey parker', tail: '' });
    assert.strictEqual(phase(env), 'connecting', 'the pop begins immediately');
    await env.flush();
    const live = liveSockets(env)[0];
    assert.ok(live, 'the realtime line opens');
    assert.ok(!wakeWs.closed, 'the wake lane stays open for the request tail until the line is up');
    live.open();
    assert.ok(wakeWs.closed, 'the wake lane closes once the line is open');
    assert.deepStrictEqual(live.sent[0], { type: 'hello', tail: '' }, 'hello is the first frame');
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
    await poweredOff(env);
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

  await test('a missing local wake model fails closed: power off, no cloud line, honest card', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    wakeWs.message({ type: 'unavailable', text: 'Wake listening needs the local voice model.' });
    await env.flush();
    assert.strictEqual(liveSockets(env).length, 0, 'NEVER continuous cloud audio as a fallback');
    assert.strictEqual(power(env), 'off');
    assert.ok(env.streams[0].track.stopped, 'microphone released');
    assert.strictEqual(env.settings.power_on, false, 'off is persisted through the authority');
    const c = card(env);
    assert.ok(c && c.region === 'alert' && /local voice model/i.test(c.text) && /stayed off/i.test(c.text), JSON.stringify(c));
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

  await test('a second drop in one activation rests honestly — no third line, no loop', async () => {
    const env = await bootedEnv();
    const ws = await poweredActive(env);
    ws.dropped();
    env.advance(2600);
    await env.flush();
    const retry = liveSockets(env)[liveSockets(env).length - 1];
    assert.notStrictEqual(retry, ws, 'one quiet retry');
    retry.open();
    const wakeBefore = wakeSockets(env).length;
    retry.dropped();
    await env.flush();
    env.advance(5000);
    await env.flush();
    assert.strictEqual(liveSockets(env).length, 2, 'no third socket, ever, without his wake');
    assert.strictEqual(power(env), 'dormant', 'back to rest — local wake still works');
    assert.strictEqual(wakeSockets(env).length, wakeBefore + 1, 'wake re-armed');
    assert.ok(card(env) && /hey parker/i.test(card(env).text), 'the honest way back is named');
    // …and his next "hey parker" re-arms exactly one retry.
    const wakeWs = wakeSockets(env)[wakeSockets(env).length - 1];
    wakeWs.open();
    wakeWs.message({ type: 'wake', heard: 'hey parker', matched: 'hey parker', tail: '' });
    await env.flush();
    assert.strictEqual(liveSockets(env).length, 3);
  });

  await test('the switch shows ON only after the engine acknowledges the claim', async () => {
    const env = await bootedEnv();
    const promise = env.context.powerOn();
    assert.strictEqual(power(env), 'starting');
    assert.strictEqual(env.element('power').getAttribute('aria-checked'), 'false', 'not "on" until the engine says so');
    await promise;
    await env.flush();
    assert.strictEqual(env.powerClaims.length, 1, 'one claim');
    assert.ok(env.powerClaims[0].client_id, 'the page identifies itself');
    // Claim happened BEFORE the microphone was touched.
    const claimIndex = env.fetches.findIndex((f) => String(f.url).includes('/companion/power'));
    assert.ok(claimIndex >= 0 && env.streams.length === 1);
    const wakeWs = wakeSockets(env)[0];
    assert.strictEqual(query(wakeWs.url).owner, 'tok-1', 'the wake lane presents the owner token');
    assert.strictEqual(query(wakeWs.url).gen, '1', '…and the generation');
    wakeWs.open();
    assert.strictEqual(power(env), 'dormant');
  });

  await test('a boot claim refused by a reload race retries once before believing "elsewhere"', async () => {
    const env = await bootedEnv({ power_on: true });
    // bootedEnv already booted with grant; simulate a fresh boot whose first
    // claim meets the OLD page's still-registered wake socket.
    const env2 = createEnv();
    env2.settings.power_on = true;
    env2.powerMode = 'elsewhere';
    const booted = env2.boot(pageScript);
    await booted; await env2.flush();
    assert.strictEqual(env2.powerClaims.length, 1, 'first claim refused');
    env2.powerMode = 'grant'; // the old socket has gone by now
    env2.advance(1600); await env2.flush(); await env2.flush();
    assert.strictEqual(env2.powerClaims.length, 2, 'one patient retry');
    assert.strictEqual(env2.document.body.dataset.power, 'dormant', 'and Parker rests, not "elsewhere"');
    void env;
  });

  await test('a refused reconnect after an engine restart re-claims and keeps resting', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    // The engine restarted: durable switch still ON, nobody owns it.
    env.settings.power_on = true;
    env.ownerClient = '';
    wakeWs.message({ type: 'revoked', reason: 'power_off' });
    await env.flush(); await env.flush(); await env.flush();
    assert.strictEqual(env.powerClaims.length, 2, 're-claimed once');
    assert.strictEqual(power(env), 'dormant', 'still resting');
    assert.ok(!env.streams[0].track.stopped, 'the mic was never released');
    assert.strictEqual(wakeSockets(env).length, 2, 'a fresh wake lane with the new credentials');
    assert.strictEqual(env.powerReleases.length, 0);
    // …but a REAL power-off elsewhere (someone owns it / switch off) still turns us off.
    const second = wakeSockets(env)[1]; second.open();
    env.settings.power_on = false;
    second.message({ type: 'revoked', reason: 'power_off' });
    await env.flush(); await env.flush(); await env.flush();
    assert.strictEqual(power(env), 'off');
    assert.ok(/turned off/i.test(card(env).text));
  });

  await test('the live line presents the same owner credentials', async () => {
    const env = await bootedEnv();
    const live = await poweredActive(env);
    assert.strictEqual(query(live.url).owner, 'tok-1');
    assert.strictEqual(query(live.url).gen, '1');
  });

  await test('a claim refused because another screen is listening opens nothing', async () => {
    const env = await bootedEnv();
    env.powerMode = 'elsewhere';
    await env.context.powerOn();
    await env.flush();
    assert.strictEqual(env.streams.length, 0, 'no microphone');
    assert.strictEqual(env.sockets.length, 0, 'no sockets');
    assert.strictEqual(power(env), 'elsewhere');
    assert.ok(/another screen/i.test(card(env).text));
    assert.ok(/another screen/i.test(env.element('sr-status').textContent));
    // The switch here turns Parker off EVERYWHERE.
    env.powerMode = 'grant';
    click(env, 'power');
    await env.flush();
    assert.strictEqual(env.powerReleases.length, 1, 'an off request went to the engine');
    assert.strictEqual(power(env), 'off');
  });

  await test('a failed power-on write leaves Parker OFF with an honest card', async () => {
    const env = await bootedEnv();
    env.powerMode = 'fail';
    await env.context.powerOn();
    await env.flush();
    assert.strictEqual(env.streams.length, 0, 'nothing was acquired');
    assert.strictEqual(power(env), 'off');
    const c = card(env);
    assert.ok(c && c.region === 'alert' && /save/i.test(c.text), JSON.stringify(c));
    assert.strictEqual(env.settings.power_on, false);
  });

  await test('an unreachable engine at power-on is said out loud, nothing listens', async () => {
    const env = await bootedEnv();
    env.powerMode = 'unreachable';
    await env.context.powerOn();
    await env.flush();
    assert.strictEqual(env.streams.length, 0);
    assert.strictEqual(power(env), 'off');
    assert.ok(/reach its engine/i.test(card(env).text));
  });

  await test('a power-off write that fails keeps everything dead, retries, then says so', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    env.offSave = false;
    await poweredOff(env);
    assert.ok(env.streams[0].track.stopped && wakeWs.closed, 'released first, regardless');
    assert.strictEqual(power(env), 'off');
    assert.strictEqual(env.powerReleases.length, 1);
    env.advance(1100); await env.flush();
    env.advance(3100); await env.flush();
    env.advance(8100); await env.flush();
    assert.strictEqual(env.powerReleases.length, 4, 'three bounded retries');
    env.advance(30000); await env.flush();
    assert.strictEqual(env.powerReleases.length, 4, 'then it stops');
    const c = card(env);
    assert.ok(c && c.region === 'alert' && /didn’t save/.test(c.text), JSON.stringify(c));
    assert.strictEqual(env.streams.length, 1, 'nothing was re-acquired');
  });

  await test('a revoked wake lane (Parker turned off elsewhere) turns this screen off without posting off', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    wakeWs.message({ type: 'revoked', reason: 'power_off' });
    await env.flush();
    assert.strictEqual(power(env), 'off');
    assert.ok(env.streams[0].track.stopped, 'microphone released');
    assert.strictEqual(env.powerReleases.length, 0, 'the engine already did it — never turn off a new owner');
    assert.ok(/turned off/i.test(card(env).text));
    assert.strictEqual(wakeSockets(env).length, 1, 'no wake-lane retry after a revocation');
  });

  await test('a revoked live line is not a drop: no retry, honest card', async () => {
    const env = await bootedEnv();
    const live = await poweredActive(env);
    live.message({ type: 'revoked', reason: 'superseded' });
    await env.flush();
    live.dropped(); // the engine closes it after the frame
    env.advance(5000);
    await env.flush();
    assert.strictEqual(liveSockets(env).length, 1, 'no reconnect');
    assert.strictEqual(power(env), 'off');
    assert.ok(/another screen/i.test(card(env).text));
    assert.strictEqual(env.powerReleases.length, 0);
  });

  await test('a wake frame carrying the request tail rides the hello, plus what the lane heard after', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    wakeWs.message({ type: 'wake', heard: 'hey parker can you', matched: 'hey parker', tail: 'can you' });
    await env.flush();
    // Mic frames keep going to the WAKE lane while the line connects.
    assert.ok(env.micFrame(0.2));
    assert.ok(wakeWs.sent.some((f) => f.type === 'audio'), 'frames still reach the wake lane');
    // The engine cleared its window at the wake: tail frames hold only what
    // came AFTER "can you" — the page keeps the wake frame's words in front.
    wakeWs.message({ type: 'tail', text: 'help me with' });
    wakeWs.message({ type: 'tail', text: 'help me with the tv' });
    const live = liveSockets(env)[0];
    assert.strictEqual(live.sent.length, 0, 'nothing to the line before it opens');
    live.open();
    assert.deepStrictEqual(live.sent[0], { type: 'hello', tail: 'can you help me with the tv' });
    assert.ok(wakeWs.closed);
    env.micFrame(0.2);
    assert.ok(live.sent.some((f) => f.type === 'audio'), 'after open, frames go to the line');
  });

  await test('a line that never opens still ends the wake lane within its bound', async () => {
    const env = await bootedEnv();
    const wakeWs = await poweredDormant(env);
    wakeWs.message({ type: 'wake', heard: 'hey parker', matched: 'hey parker', tail: '' });
    await env.flush();
    env.advance(3100);
    assert.ok(wakeWs.closed, 'bounded: the tail lane cannot outlive its window');
  });

  await test('cards are live regions: offers/outcomes polite, failures and errors assertive', async () => {
    const env = await bootedEnv();
    const ws = await poweredActive(env);
    // (The regions' ARIA attributes live in the HTML and are pinned by the
    // Python page test; this pins which region each kind lands in.)
    ws.message({ type: 'proposal_staged', label: 'a reminder', readback: 'a reminder' });
    assert.strictEqual(card(env).region, 'card');
    ws.message({ type: 'action_result', status: 'executed', label: 'a reminder' });
    assert.strictEqual(card(env).region, 'card');
    ws.message({ type: 'action_result', status: 'failed', label: 'a reminder' });
    const c = card(env);
    assert.strictEqual(c.region, 'alert');
    assert.ok(env.element('card').hidden, 'one visible card at a time');
    ws.message({ type: 'guard_redirect', text: 'That one is for your doctor.' });
    assert.strictEqual(card(env).region, 'alert');
  });

  await test('CC on shows "Checked the web" with bounded source labels; CC off shows nothing', async () => {
    const env = await bootedEnv();
    const ws = await poweredActive(env);
    const items = [
      { label: 'US Open official site with a very long label that keeps going', url: 'https://x' },
      { label: 'ESPN', url: 'https://y' }, { label: 'Reuters', url: 'https://z' }, { label: 'Fourth', url: 'https://w' },
    ];
    ws.message({ type: 'sources', items });
    assert.ok(env.element('cc-source').hidden, 'CC off: zero chrome');
    click(env, 'cc-toggle');
    ws.message({ type: 'sources', items });
    const line = env.element('cc-source');
    assert.ok(!line.hidden);
    assert.ok(/^Checked the web/.test(line.textContent), line.textContent);
    assert.ok(!/Fourth/.test(line.textContent), 'at most three labels');
    assert.ok(!/https/.test(line.textContent), 'never a URL');
    assert.ok(line.textContent.length < 140, 'bounded');
    env.advance(12100);
    assert.ok(line.hidden, 'expires on its own');
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
