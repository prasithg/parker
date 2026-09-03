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

test('stop is terminal for the ENTIRE event vocabulary', () => {
  // Proven over the machine's actual registered handlers, not a guess
  // list (the guess list omitted repair_offered — independent review,
  // 2026-09-01). The only events allowed to leave `stopped` are the ones
  // a person can genuinely cause from rest: a new session, page
  // readiness, a typed turn, or a real error/offline report.
  const allowedFromStopped = new Set([
    'connect', 'ready', 'user_transcript', 'error', 'offline', 'stopped',
    // The powered-on companion re-arms wake listening after a session
    // ends — dormancy is a deliberate page-asserted state, not a stale
    // session event. (wake_detected itself stays fenced to dormant.)
    'dormant',
  ]);
  const c = makeController();
  assert.ok(c.events.length >= 20, 'vocabulary introspection lost events');
  for (const name of c.events) {
    if (allowedFromStopped.has(name)) continue;
    const fresh = makeController();
    fresh.handleEvent('connect', { mode: 'live' });
    fresh.handleEvent('connected');
    fresh.handleEvent('stopped');
    assert.strictEqual(fresh.handleEvent(name, { kind: 'search' }), false, name);
    const s = fresh.getState();
    assert.strictEqual(s.phase, 'stopped', name);
    assert.deepStrictEqual(s.work, [], name);
    assert.strictEqual(s.action, 'none', name);
    assert.strictEqual(s.guard, 'none', name);
    assert.strictEqual(s.attention, 'none', name);
  }
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

test('terminal rest clears work, staged action, guard, and attention', () => {
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
  assert.strictEqual(s.attention, 'none');
});

// ---------------------------------------------------------------------------
// Attention: waiting-for-choice / waiting-for-confirmation are DURABLE
// (independent review, 2026-09-01 — the repair/confirmation pose must not
// vanish at TTS drain while the cards still wait on screen)
// ---------------------------------------------------------------------------

function drainToIdle(c) {
  c.handleEvent('assistant_audio');
  c.handleEvent('assistant_audio_drained');
  assert.strictEqual(c.getState().phase, 'idle');
}

test('choices survive playback draining to idle', () => {
  const c = makeController();
  c.handleEvent('user_transcript'); // typed/spoken turn from rest
  c.handleEvent('choices_offered');
  assert.strictEqual(c.getState().attention, 'choice');
  assert.strictEqual(c.getState().guard, 'repair'); // asking, attentively
  drainToIdle(c);
  assert.strictEqual(c.getState().attention, 'choice'); // still waiting
  assert.strictEqual(c.getState().guard, 'repair');
});

test('a confirmation offer is the staged/waiting state, not repair', () => {
  const c = makeController();
  c.handleEvent('user_transcript');
  c.handleEvent('yes_no_offered');
  const s = c.getState();
  assert.strictEqual(s.attention, 'confirmation');
  assert.strictEqual(s.action, 'staged'); // waiting on screen; nothing ran
  assert.strictEqual(s.guard, 'none');
  drainToIdle(c);
  assert.strictEqual(c.getState().attention, 'confirmation');
  assert.strictEqual(c.getState().action, 'staged');
});

test('attention_resolved clears the wait, the staged pose, and the asking face', () => {
  const c = makeController();
  c.handleEvent('user_transcript');
  c.handleEvent('choices_offered');
  drainToIdle(c);
  assert.ok(c.handleEvent('attention_resolved'));
  const s = c.getState();
  assert.strictEqual(s.attention, 'none');
  assert.strictEqual(s.guard, 'none');
  assert.strictEqual(s.action, 'none');
  assert.strictEqual(c.handleEvent('attention_resolved'), false); // idempotent
});

test('a waiting overlay expires on its own TTL as the safety net', () => {
  const c = makeController({ attentionTtlMs: 120000 });
  c.handleEvent('user_transcript');
  c.handleEvent('yes_no_offered');
  drainToIdle(c);
  clock = 121000;
  c.tick();
  assert.strictEqual(c.getState().attention, 'none');
  assert.strictEqual(c.getState().action, 'none');
});

test('proposal_staged also means waiting for confirmation', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('proposal_staged');
  assert.strictEqual(c.getState().attention, 'confirmation');
});

test('waiting labels never overclaim', () => {
  const choice = ParkerExpression.describe({
    phase: 'idle', work: [], action: 'none', guard: 'repair', attention: 'choice',
  });
  assert.ok(/choice|number/i.test(choice), choice);
  const confirm = ParkerExpression.describe({
    phase: 'idle', work: [], action: 'none', guard: 'none', attention: 'confirmation',
  });
  assert.ok(/nothing has happened/i.test(confirm), confirm);
  assert.ok(!/done|executed|sent|saved/i.test(confirm), confirm);
});

test('subscribers learn the CAUSE of each change', () => {
  const c = makeController();
  const causes = [];
  c.subscribe((s, cause) => causes.push(cause));
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.setEnergy({ user: 0.2 });
  clock = 200;
  c.setEnergy({ user: 0.2 }); // hearing via energy
  c.handleEvent('guard_redirect');
  clock = 30000;
  c.tick(); // guard TTL expiry
  assert.deepStrictEqual(causes, ['connect', 'connected', 'energy', 'guard_redirect', 'tick']);
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

test('concurrent same-kind lookups only clear when the LAST one finishes', () => {
  // The stress deck's real shape: several look_that_up calls in flight at
  // once. One completion must not silently drop the claim of ongoing work.
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  for (let i = 0; i < 3; i++) c.handleEvent('work_start', { kind: 'search' });
  c.handleEvent('work_done', { kind: 'search' });
  assert.deepStrictEqual(c.getState().work, ['search']);
  c.handleEvent('work_failed', { kind: 'search' });
  assert.deepStrictEqual(c.getState().work, ['search']);
  c.handleEvent('work_done', { kind: 'search' });
  assert.deepStrictEqual(c.getState().work, []);
  assert.strictEqual(c.handleEvent('work_done', { kind: 'search' }), false);
});

test('proposal_staged sets the waiting-on-screen overlay', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  assert.ok(c.handleEvent('proposal_staged'));
  assert.strictEqual(c.getState().action, 'staged');
});

test('executed is reachable ONLY through the real outcome frame', () => {
  // The 2026-08-31 brief forbade any executed claim until a real
  // pipeline signal existed; companion take 2 (2026-09-01) added exactly
  // one: the bridge's action_result frame, mapped to action_executed /
  // action_failed. Proven over the machine's ACTUAL vocabulary: no OTHER
  // event — and no amount of ticking — may ever claim execution.
  const c = makeController();
  assert.ok(Array.isArray(c.events) && c.events.length >= 15);
  const executionEvents = c.events.filter((n) => /exec/.test(n));
  assert.deepStrictEqual(executionEvents, ['action_executed']);
  for (const name of c.events) {
    if (name === 'action_executed') continue;
    const fresh = makeController();
    fresh.handleEvent('connect', { mode: 'live' });
    fresh.handleEvent('connected');
    fresh.handleEvent('proposal_staged');
    fresh.handleEvent(name, { kind: 'search', mode: 'live' });
    assert.notStrictEqual(fresh.getState().action, 'executed', name);
    fresh.tick();
    assert.notStrictEqual(fresh.getState().action, 'executed', name);
  }
});

test('a real outcome frame lands and then relaxes; the record is durable elsewhere', () => {
  const c = makeController({ resultTtlMs: 12000 });
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('proposal_staged');
  assert.ok(c.handleEvent('action_executed'));
  assert.strictEqual(c.getState().action, 'executed');
  assert.strictEqual(c.getState().attention, 'none'); // the wait is over
  clock = 13000;
  c.tick();
  assert.strictEqual(c.getState().action, 'none'); // a brief acknowledgment only

  const f = makeController({ resultTtlMs: 12000 });
  f.handleEvent('connect', { mode: 'live' });
  f.handleEvent('connected');
  f.handleEvent('proposal_staged');
  assert.ok(f.handleEvent('action_failed'));
  assert.strictEqual(f.getState().action, 'failed');
});

test('outcome frames are rejected outside an active session', () => {
  const c = makeController();
  assert.strictEqual(c.handleEvent('action_executed'), false);
  assert.strictEqual(c.getState().action, 'none');
});

test('the spoken-confirmation labels never overclaim', () => {
  const staged = ParkerExpression.describe({
    phase: 'listening', work: [], action: 'staged', guard: 'none', attention: 'confirmation',
  });
  assert.ok(/say yes/i.test(staged), staged);
  assert.ok(/nothing has happened/i.test(staged), staged);
  assert.ok(!/tap|button|press|touch/i.test(staged), staged); // voice is the interface
  const failed = ParkerExpression.describe({
    phase: 'listening', work: [], action: 'failed', guard: 'none', attention: 'none',
  });
  assert.ok(!/done|worked/i.test(failed), failed);
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
// Dormancy and wake (docs/plans/2026-09-01-wake-word.md)
// ---------------------------------------------------------------------------

test('dormant is a cleared rest state; only wake_detected pops it', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  c.handleEvent('work_start', { kind: 'search' });
  c.handleEvent('proposal_staged');
  assert.ok(c.handleEvent('dormant'));
  const s = c.getState();
  assert.strictEqual(s.phase, 'dormant');
  assert.deepStrictEqual(s.work, []);
  assert.strictEqual(s.action, 'none');
  assert.strictEqual(s.attention, 'none');
  // Ambient events cannot animate a dormant scene…
  for (const name of ['assistant_audio', 'work_start', 'proposal_staged',
                      'choices_offered', 'interrupted', 'connected']) {
    assert.strictEqual(c.handleEvent(name, { kind: 'search' }), false, name);
  }
  // …and energy is ignored (someone talking near the mic is not a wake).
  clock = 5000;
  c.setEnergy({ user: 1.0 });
  clock = 6000;
  c.setEnergy({ user: 1.0 });
  assert.strictEqual(c.getState().phase, 'dormant');
  // The real local detection is the ONE way out — into a live session.
  assert.ok(c.handleEvent('wake_detected'));
  assert.strictEqual(c.getState().phase, 'connecting');
  assert.strictEqual(c.getState().mode, 'live');
  assert.ok(c.handleEvent('connected'));
  assert.strictEqual(c.getState().phase, 'listening');
});

test('wake_detected is rejected outside dormancy', () => {
  for (const setup of [['ready'], ['stopped'], ['offline'],
                       ['connect', 'connected']]) {
    const c = makeController();
    for (const step of setup) c.handleEvent(step, { mode: 'live' });
    assert.strictEqual(c.handleEvent('wake_detected'), false, setup.join(','));
  }
});

test('the dormant label invites the wake phrase without overclaiming', () => {
  const text = ParkerExpression.describe({
    phase: 'dormant', work: [], action: 'none', guard: 'none', attention: 'none',
  });
  assert.ok(/hey parker/i.test(text), text);
  assert.ok(!/listening intently|talking/i.test(text), text);
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

test('a phrase boundary is a beat only while talking, never a phase change', () => {
  const c = makeController();
  c.handleEvent('connect', { mode: 'live' });
  c.handleEvent('connected');
  assert.strictEqual(c.getState().beats, 0);
  assert.strictEqual(c.handleEvent('phrase_boundary'), false); // listening: nothing
  assert.strictEqual(c.getState().beats, 0);
  c.handleEvent('user_transcript');
  c.handleEvent('assistant_audio');
  assert.strictEqual(c.getState().phase, 'talking');
  assert.strictEqual(c.handleEvent('phrase_boundary'), true);
  assert.strictEqual(c.handleEvent('phrase_boundary'), true);
  assert.strictEqual(c.getState().beats, 2);
  assert.strictEqual(c.getState().phase, 'talking', 'a beat never changes the phase');
});

const failed = results.filter((r) => !r.ok);
for (const r of results) {
  process.stdout.write((r.ok ? 'ok  ' : 'FAIL') + '  ' + r.name + (r.ok ? '' : '  — ' + r.error) + '\n');
}
process.stdout.write('\n' + (results.length - failed.length) + '/' + results.length + ' passed\n');
process.exit(failed.length ? 1 : 0);
