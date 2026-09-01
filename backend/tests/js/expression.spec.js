/*
 * Unit tests for ParkerExpression (app/parker/static/converse/expression.js).
 *
 * Run by tests/test_expression_state.py via `node`; exits non-zero on the
 * first failure. Plain node asserts — no test framework, no DOM, no WebGL,
 * matching the brief's requirement that the expression state unit-tests
 * without a renderer.
 */
'use strict';

const assert = require('assert');
const path = require('path');

const ParkerExpression = require(
  path.join(__dirname, '..', '..', 'app', 'parker', 'static', 'converse', 'expression.js')
);

let clock = 0;
function makeController(extra) {
  clock = 0;
  return ParkerExpression.createController(Object.assign({ now: () => clock }, extra || {}));
}

const results = [];
function test(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (err) {
    results.push({ name, ok: false, error: err && err.message });
  }
}

// ---------------------------------------------------------------------------
// Phase transitions from real events
// ---------------------------------------------------------------------------

test('starts idle', () => {
  const c = makeController();
  assert.strictEqual(c.getState().phase, 'idle');
  assert.strictEqual(c.getState().mode, null);
});

test('connect -> connected reaches listening in live mode', () => {
  const c = makeController();
  assert.ok(c.handleEvent('connect', { mode: 'live' }));
  assert.strictEqual(c.getState().phase, 'connecting');
  assert.strictEqual(c.getState().mode, 'live');
  assert.ok(c.handleEvent('connected'));
  assert.strictEqual(c.getState().phase, 'listening');
});

test('a stale connected frame is rejected outside connecting', () => {
  const c = makeController();
  assert.strictEqual(c.handleEvent('connected'), false);
  assert.strictEqual(c.getState().phase, 'idle');
});

test('transcript means thinking; scheduled audio means talking', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  assert.ok(c.handleEvent('user_transcript'));
  assert.strictEqual(c.getState().phase, 'thinking');
  assert.ok(c.handleEvent('assistant_audio'));
  assert.strictEqual(c.getState().phase, 'talking');
});

test('drained audio returns to listening in live, idle in turns', () => {
  const live = makeController();
  live.handleEvent('connect', { mode: 'live' });
  live.handleEvent('connected');
  live.handleEvent('assistant_audio');
  assert.ok(live.handleEvent('assistant_audio_drained'));
  assert.strictEqual(live.getState().phase, 'listening');

  const turns = makeController();
  turns.handleEvent('connect', { mode: 'turns' });
  turns.handleEvent('connected');
  turns.handleEvent('user_transcript');
  turns.handleEvent('assistant_audio');
  turns.handleEvent('assistant_audio_drained');
  assert.strictEqual(turns.getState().phase, 'idle');
});

test('drained is ignored unless actually talking', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  assert.strictEqual(c.handleEvent('assistant_audio_drained'), false);
  assert.strictEqual(c.getState().phase, 'listening');
});

// ---------------------------------------------------------------------------
// Interruption / barge-in
// ---------------------------------------------------------------------------

test('interruption yields only from talking or thinking', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  // The live lane sends `clear` on every speech_started — nothing to
  // yield while merely listening.
  assert.strictEqual(c.handleEvent('interrupted'), false);
  c.handleEvent('assistant_audio');
  assert.ok(c.handleEvent('interrupted'));
  assert.strictEqual(c.getState().phase, 'interrupted');
});

test('the interrupt dwell settles back to listening on tick', () => {
  const c = makeController({ interruptDwellMs: 500 });
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('assistant_audio');
  c.handleEvent('interrupted');
  clock = 400;
  c.tick();
  assert.strictEqual(c.getState().phase, 'interrupted');
  clock = 600;
  c.tick();
  assert.strictEqual(c.getState().phase, 'listening');
});

test('new audio during the yield goes straight back to talking', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('assistant_audio');
  c.handleEvent('interrupted');
  assert.ok(c.handleEvent('assistant_audio'));
  assert.strictEqual(c.getState().phase, 'talking');
});

// ---------------------------------------------------------------------------
// Hearing derives from real mic energy with hysteresis
// ---------------------------------------------------------------------------

test('sustained mic energy raises hearing; sustained quiet releases it', () => {
  const c = makeController({ hearingAttackMs: 100, hearingReleaseMs: 500 });
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');

  c.setEnergy({ user: 0.2 });
  assert.strictEqual(c.getState().phase, 'listening'); // attack not yet met
  clock = 120;
  c.setEnergy({ user: 0.2 });
  assert.strictEqual(c.getState().phase, 'hearing');

  clock = 200;
  c.setEnergy({ user: 0.0 });
  assert.strictEqual(c.getState().phase, 'hearing'); // release not yet met
  clock = 800;
  c.setEnergy({ user: 0.0 });
  assert.strictEqual(c.getState().phase, 'listening');
});

test('a blip below the exit threshold does not release hearing', () => {
  const c = makeController({ hearingAttackMs: 100, hearingReleaseMs: 500 });
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.setEnergy({ user: 0.2 });
  clock = 150;
  c.setEnergy({ user: 0.2 });
  assert.strictEqual(c.getState().phase, 'hearing');
  clock = 300;
  c.setEnergy({ user: 0.0 });
  clock = 400;
  c.setEnergy({ user: 0.3 }); // he resumed before the release window
  clock = 900;
  c.setEnergy({ user: 0.3 });
  assert.strictEqual(c.getState().phase, 'hearing');
});

test('energy never drives hearing outside listening/hearing', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('user_transcript');
  clock = 5000;
  c.setEnergy({ user: 1.0 });
  clock = 6000;
  c.setEnergy({ user: 1.0 });
  assert.strictEqual(c.getState().phase, 'thinking');
});

test('energy values clamp to [0, 1]', () => {
  const c = makeController();
  c.setEnergy({ user: 7, parker: -3 });
  assert.strictEqual(c.getState().userEnergy, 1);
  assert.strictEqual(c.getState().parkerEnergy, 0);
});

// ---------------------------------------------------------------------------
// Close / Stop / stale-event rejection
// ---------------------------------------------------------------------------

test('closing then closed reaches stopped', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  assert.ok(c.handleEvent('closing'));
  assert.strictEqual(c.getState().phase, 'closing');
  assert.ok(c.handleEvent('closed'));
  assert.strictEqual(c.getState().phase, 'stopped');
});

test('stop is terminal: late session events cannot re-animate the scene', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('stopped');
  for (const name of [
    'assistant_audio', 'work_start', 'proposal_staged',
    'guard_redirect', 'closing', 'interrupted', 'assistant_audio_drained',
  ]) {
    assert.strictEqual(c.handleEvent(name, { kind: 'search' }), false, name);
  }
  const s = c.getState();
  assert.strictEqual(s.phase, 'stopped');
  assert.deepStrictEqual(s.work, []);
  assert.strictEqual(s.action, 'none');
});

test('a genuine typed turn starts a turns session from rest', () => {
  // user_transcript is the one event a person can cause from rest (the
  // Type-instead flow); the page never forwards it for stale turns, and
  // late live-socket frames are dropped page-side by socket identity.
  for (const from of ['idle', 'stopped', 'error']) {
    const c = makeController();
    if (from !== 'idle') c.handleEvent(from === 'stopped' ? 'stopped' : 'error');
    assert.ok(c.handleEvent('user_transcript'), from);
    assert.strictEqual(c.getState().phase, 'thinking', from);
    assert.strictEqual(c.getState().mode, 'turns', from);
    // ...and the rest of the typed turn animates: talking, then idle.
    assert.ok(c.handleEvent('assistant_audio'), from);
    assert.ok(c.handleEvent('assistant_audio_drained'), from);
    assert.strictEqual(c.getState().phase, 'idle', from);
  }
});

test('a new connect after stop starts a fresh session', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('stopped');
  assert.ok(c.handleEvent('connect', { mode: 'live' }));
  assert.ok(c.handleEvent('connected'));
  assert.strictEqual(c.getState().phase, 'listening');
});

test('leaving the session clears work, staged action, and guard', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('work_start', { kind: 'search' });
  c.handleEvent('proposal_staged');
  c.handleEvent('guard_redirect');
  c.handleEvent('stopped');
  const s = c.getState();
  assert.deepStrictEqual(s.work, []);
  assert.strictEqual(s.action, 'none');
  assert.strictEqual(s.guard, 'none');
});

// ---------------------------------------------------------------------------
// Work overlay: real dispatch/completion plus a TTL safety net
// ---------------------------------------------------------------------------

test('work overlay tracks start and done per kind', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('work_start', { kind: 'context' });
  c.handleEvent('work_start', { kind: 'search' });
  assert.deepStrictEqual(c.getState().work, ['context', 'search']);
  c.handleEvent('work_done', { kind: 'context' });
  assert.deepStrictEqual(c.getState().work, ['search']);
  c.handleEvent('work_failed', { kind: 'search' });
  assert.deepStrictEqual(c.getState().work, []);
});

test('work_done for unknown work is rejected', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  assert.strictEqual(c.handleEvent('work_done', { kind: 'search' }), false);
});

test('a lost completion frame expires: no eternal work claim', () => {
  const c = makeController({ workTtlMs: 45000 });
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('work_start', { kind: 'search' });
  clock = 44000;
  c.tick();
  assert.deepStrictEqual(c.getState().work, ['search']);
  clock = 46000;
  c.tick();
  assert.deepStrictEqual(c.getState().work, []);
});

test('work cannot start outside a session', () => {
  const c = makeController();
  assert.strictEqual(c.handleEvent('work_start', { kind: 'search' }), false);
});

// ---------------------------------------------------------------------------
// Action overlay: staged only, never executed
// ---------------------------------------------------------------------------

test('proposal_staged sets the waiting-on-screen overlay', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  assert.ok(c.handleEvent('proposal_staged'));
  assert.strictEqual(c.getState().action, 'staged');
});

test('no event path can ever claim an executed action', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  for (const name of ['action_executed', 'executed', 'action_done', 'confirmed']) {
    assert.strictEqual(c.handleEvent(name), false, name);
  }
  assert.strictEqual(c.getState().action, 'none');
});

test('the staged pose relaxes after its TTL; the card is the durable truth', () => {
  const c = makeController({ actionTtlMs: 120000 });
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('proposal_staged');
  clock = 121000;
  c.tick();
  assert.strictEqual(c.getState().action, 'none');
});

// ---------------------------------------------------------------------------
// Guard / repair overlay
// ---------------------------------------------------------------------------

test('guard redirect shows concern and clears when he speaks again', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('guard_redirect');
  assert.strictEqual(c.getState().guard, 'redirect');
  c.handleEvent('user_transcript');
  assert.strictEqual(c.getState().guard, 'none');
});

test('guard expires on its own if he never replies', () => {
  const c = makeController({ guardTtlMs: 20000 });
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('guard_redirect');
  clock = 21000;
  c.tick();
  assert.strictEqual(c.getState().guard, 'none');
});

test('repair posture sets and resolves', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'turns' });
  c.handleEvent('connected');
  c.handleEvent('repair_offered');
  assert.strictEqual(c.getState().guard, 'repair');
  c.handleEvent('repair_resolved');
  assert.strictEqual(c.getState().guard, 'none');
});

// ---------------------------------------------------------------------------
// Subscription semantics
// ---------------------------------------------------------------------------

test('subscribers fire on semantic change only, never on plain energy', () => {
  const c = makeController();
  const seen = [];
  c.subscribe((s) => seen.push(s.phase));
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  const before = seen.length;
  c.setEnergy({ user: 0.01 });
  c.setEnergy({ parker: 0.5 });
  c.handleEvent('notice');
  assert.strictEqual(seen.length, before);
  c.handleEvent('user_transcript');
  assert.strictEqual(seen.length, before + 1);
  assert.strictEqual(seen[seen.length - 1], 'thinking');
});

test('unsubscribe stops delivery', () => {
  const c = makeController();
  const seen = [];
  const off = c.subscribe((s) => seen.push(s.phase));
  c.handleEvent('connect', { mode: 'live' });
  off();
  c.handleEvent('connected');
  assert.deepStrictEqual(seen, ['connecting']);
});

// ---------------------------------------------------------------------------
// Labels: the words may never overclaim
// ---------------------------------------------------------------------------

test('every phase has a plain-language label', () => {
  for (const phase of ParkerExpression.PHASES) {
    const text = ParkerExpression.describe({ phase, work: [], action: 'none', guard: 'none' });
    assert.ok(text && text.length > 0, phase);
  }
});

test('the staged label says nothing has happened yet', () => {
  const text = ParkerExpression.describe({
    phase: 'listening', work: [], action: 'staged', guard: 'none',
  });
  assert.ok(/nothing has happened/i.test(text), text);
  assert.ok(!/done|executed|sent|saved/i.test(text), text);
});

test('search overlay reads as checking, not as an answer', () => {
  const text = ParkerExpression.describe({
    phase: 'thinking', work: ['search'], action: 'none', guard: 'none',
  });
  assert.ok(/checking/i.test(text), text);
});

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

const failed = results.filter((r) => !r.ok);
for (const r of results) {
  process.stdout.write((r.ok ? 'ok  ' : 'FAIL') + '  ' + r.name + (r.ok ? '' : '  — ' + r.error) + '\n');
}
process.stdout.write('\n' + (results.length - failed.length) + '/' + results.length + ' passed\n');
process.exit(failed.length ? 1 : 0);
