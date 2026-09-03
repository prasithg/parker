/*
 * Executable lifecycle tests for the REAL lab page script (converse_ui.py
 * — the Start/Done/type developer & accessibility harness at
 * /parker/converse/lab; the live lane lives on the companion page and is
 * pinned by companion_page.spec.js).
 *
 * Usage: node converse_page.spec.js <path-to-extracted-inline-script>
 */
'use strict';

const assert = require('assert');
const { createEnv } = require('./converse_page_env');
const { extractedPageScripts } = require('./page_script_fixture');

let pageScript = process.argv[2];
if (!pageScript) {
  [pageScript] = extractedPageScripts('lab');
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
  await env.flush();
  return env;
}

function phase(env) {
  return env.context.ParkerPresence.controller.getState().phase;
}

(async () => {
  await test('boot reaches idle with the push-button controls', async () => {
    const env = await bootedEnv();
    assert.strictEqual(env.document.body.dataset.state, 'idle');
    assert.ok(!env.element('btn-start').hidden, 'Start listening is offered');
    assert.strictEqual(phase(env), 'idle');
  });

  await test('capture releases fully on page hide', async () => {
    const env = await bootedEnv();
    await env.context.startListening();
    await env.flush();
    assert.strictEqual(env.streams.length, 1, 'the microphone opened');
    assert.strictEqual(env.document.body.dataset.state, 'listening');
    const scene = { disposed: false, dispose() { this.disposed = true; } };
    env.context.ParkerPresence.scene = scene;
    env.firePagehide();
    assert.ok(env.streams[0].track.stopped, 'mic track released');
    for (const ctx of env.audioContexts) assert.ok(ctx.closed, 'audio context closed');
    assert.strictEqual(env.intervalCount(), 0, 'no timer survives page hide');
    assert.ok(scene.disposed, 'the GL scene is disposed');
    assert.ok(env.beacons.some((b) => b.url.includes('/end')));
    env.firePageshow(true);
    assert.strictEqual(env.reloads, 1, 'a BFCache restore reloads clean');
  });

  await test('Stop is terminal and flushes the semantic receipts', async () => {
    const env = await bootedEnv();
    await env.context.startListening();
    await env.flush();
    env.context.stopParker();
    assert.strictEqual(env.document.body.dataset.state, 'stopped');
    assert.strictEqual(phase(env), 'stopped');
    assert.ok(env.streams[0].track.stopped, 'mic released on Stop');
    const receipt = env.beacons.find((b) => b.url.includes('/receipts') && b.body.includes('expression'));
    assert.ok(receipt, 'Stop flushes the expression receipts');
    const parsed = JSON.parse(receipt.body);
    assert.strictEqual(parsed.expression[parsed.expression.length - 1].to, 'stopped');
  });

  const failed = results.filter((r) => !r.ok);
  for (const r of results) {
    process.stdout.write((r.ok ? 'ok  ' : 'FAIL') + '  ' + r.name + (r.ok ? '' : '\n' + r.error) + '\n');
  }
  process.stdout.write('\n' + (results.length - failed.length) + '/' + results.length + ' passed\n');
  process.exit(failed.length ? 1 : 0);
})();
