/*
 * ParkerExpression — Parker's semantic expression state.
 *
 * The durable product contract from docs/plans/2026-08-31-reachy-mini-
 * converse-ui.md: real voice/runtime signals -> this small semantic state
 * -> any renderer (3D Reachy Mini today, orb/static fallback, a physical
 * robot later). No renderer, Three.js, or DOM knowledge lives here, so
 * the whole machine unit-tests in Node without WebGL.
 *
 * Truthfulness rules encoded here:
 * - every phase change is caused by a real event the page observed
 *   (socket lifecycle, mic energy, transcripts, scheduled audio, clear,
 *   closing, Stop) — never by a timer pretending work exists;
 * - stale events are rejected: audio/work/transcript events arriving
 *   after Stop or after the line closed can never re-animate the scene;
 * - work overlays expire (WORK_TTL_MS > the server's 30 s worker
 *   timeout) so a lost completion event cannot claim eternal work;
 * - `action` can only ever reach "staged" from a real proposal_staged
 *   frame; nothing here may claim execution (no browser signal exists —
 *   see the brief), so "executed"/"failed" have no entry path yet.
 *
 * UMD-ish: `module.exports` under Node (tests), `window.ParkerExpression`
 * in the page.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ParkerExpression = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var PHASES = [
    'offline', 'idle', 'connecting', 'listening', 'hearing', 'thinking',
    'talking', 'interrupted', 'closing', 'stopped', 'error',
  ];

  // Phases in which a live session is actually underway: only these may
  // accept in-session events (audio, transcripts, work, proposals).
  var ACTIVE_PHASES = {
    connecting: true, listening: true, hearing: true, thinking: true,
    talking: true, interrupted: true, closing: true,
  };

  var DEFAULTS = {
    // Mic-energy hysteresis for listening <-> hearing (RMS of float PCM).
    hearingEnter: 0.04,
    hearingExit: 0.02,
    hearingAttackMs: 120,
    hearingReleaseMs: 700,
    // Visible yield after a barge-in/flush before settling back to listening.
    interruptDwellMs: 650,
    // Server workers time out at 30 s; a lost completion frame must not
    // claim work forever.
    workTtlMs: 45000,
    // A staged proposal waits on the screen; the pose relaxes after this
    // (the confirmation card itself stays until acted on).
    actionTtlMs: 120000,
    guardTtlMs: 20000,
  };

  function createController(options) {
    var opts = Object.assign({}, DEFAULTS, options || {});
    var now = opts.now || function () { return Date.now(); };

    var state = {
      phase: 'idle',
      mode: null, // 'live' | 'turns' while a session is underway
      work: {},   // kind -> started-at ms ('context' | 'search' | ...)
      action: 'none',
      guard: 'none',
    };
    var phaseSince = now();
    var actionSince = 0;
    var guardSince = 0;
    var userEnergy = 0;
    var parkerEnergy = 0;
    var energyAbove = null;  // when the mic first rose past hearingEnter
    var energyBelow = null;  // when it last fell under hearingExit
    var listeners = [];

    function snapshot() {
      var work = Object.keys(state.work).sort();
      return {
        phase: state.phase,
        mode: state.mode,
        work: work,
        action: state.action,
        guard: state.guard,
        userEnergy: userEnergy,
        parkerEnergy: parkerEnergy,
        sincePhaseMs: Math.max(0, now() - phaseSince),
      };
    }

    function emit() {
      var current = snapshot();
      for (var i = 0; i < listeners.length; i++) {
        try { listeners[i](current); } catch (err) { /* a bad listener never breaks the machine */ }
      }
    }

    function setPhase(phase) {
      if (state.phase === phase) return false;
      state.phase = phase;
      phaseSince = now();
      if (!ACTIVE_PHASES[phase]) {
        // Leaving the session: no work, no staged pose, no guard face may
        // survive into idle/stopped/error/offline.
        state.work = {};
        state.action = 'none';
        state.guard = 'none';
        energyAbove = energyBelow = null;
        state.mode = null;
      }
      return true;
    }

    function active() { return !!ACTIVE_PHASES[state.phase]; }

    var handlers = {
      // Page ready / session surface available again.
      ready: function () { return setPhase('idle'); },
      offline: function () { return setPhase('offline'); },
      error: function () { return setPhase('error'); },
      stopped: function () { return setPhase('stopped'); },

      connect: function (data) {
        state.mode = (data && data.mode) === 'turns' ? 'turns' : 'live';
        return setPhase('connecting');
      },
      connected: function () {
        if (state.phase !== 'connecting') return false; // stale open
        return setPhase('listening');
      },

      user_transcript: function () {
        if (state.phase === 'closing') return false;
        if (!active()) {
          // A typed turn is a real new interaction: it may start a turns
          // session from rest. (Late LIVE events can never reach here —
          // the page drops frames from a socket that is no longer the
          // live one, so this path only fires on genuine user turns.)
          if (state.phase !== 'idle' && state.phase !== 'stopped' && state.phase !== 'error') {
            return false;
          }
          state.mode = 'turns';
        }
        var changed = setPhase('thinking');
        // His words arrived: a guard apology or repair posture is answered.
        if (state.guard !== 'none') { state.guard = 'none'; changed = true; }
        return changed;
      },
      assistant_audio: function () {
        if (!active() || state.phase === 'closing') return false;
        return setPhase('talking');
      },
      assistant_audio_drained: function () {
        if (state.phase !== 'talking') return false;
        return setPhase(state.mode === 'turns' ? 'idle' : 'listening');
      },
      interrupted: function () {
        // Only yields when there was actually something to yield: the live
        // lane sends `clear` on every speech_started, talking or not.
        if (state.phase !== 'talking' && state.phase !== 'thinking') return false;
        return setPhase('interrupted');
      },
      closing: function () {
        if (!active()) return false;
        return setPhase('closing');
      },
      closed: function () {
        if (state.phase !== 'closing') return false;
        return setPhase('stopped');
      },

      work_start: function (data) {
        if (!active()) return false;
        var kind = (data && data.kind) || 'search';
        state.work[kind] = now();
        return true;
      },
      work_done: function (data) {
        var kind = (data && data.kind) || 'search';
        if (!(kind in state.work)) return false;
        delete state.work[kind];
        return true;
      },
      work_failed: function (data) { return handlers.work_done(data); },

      proposal_staged: function () {
        if (!active()) return false;
        state.action = 'staged';
        actionSince = now();
        return true;
      },

      guard_redirect: function () {
        if (!active()) return false;
        state.guard = 'redirect';
        guardSince = now();
        return true;
      },
      repair_offered: function () {
        // The turns lane asks a repair question with choices on screen.
        state.guard = 'repair';
        guardSince = now();
        return true;
      },
      repair_resolved: function () {
        if (state.guard === 'none') return false;
        state.guard = 'none';
        return true;
      },

      notice: function () { return false; }, // recoverable text; never a pose change
    };

    function handleEvent(name, data) {
      var handler = handlers[name];
      if (!handler) return false;
      var changed = handler(data);
      if (changed) emit();
      return changed;
    }

    // High-frequency energy inputs; hearing/listening derives from the
    // real mic level with hysteresis. Never fires subscribers by itself
    // unless the derived phase actually changes.
    function setEnergy(levels) {
      if (levels && typeof levels.user === 'number') {
        userEnergy = Math.max(0, Math.min(1, levels.user));
      }
      if (levels && typeof levels.parker === 'number') {
        parkerEnergy = Math.max(0, Math.min(1, levels.parker));
      }
      var t = now();
      if (state.phase === 'listening') {
        if (userEnergy >= opts.hearingEnter) {
          if (energyAbove === null) energyAbove = t;
          if (t - energyAbove >= opts.hearingAttackMs) {
            energyBelow = null;
            if (setPhase('hearing')) emit();
          }
        } else {
          energyAbove = null;
        }
      } else if (state.phase === 'hearing') {
        if (userEnergy <= opts.hearingExit) {
          if (energyBelow === null) energyBelow = t;
          if (t - energyBelow >= opts.hearingReleaseMs) {
            energyAbove = null;
            if (setPhase('listening')) emit();
          }
        } else {
          energyBelow = null;
        }
      } else {
        energyAbove = energyBelow = null;
      }
    }

    // Time-driven housekeeping: the interrupt yield settles, and stale
    // overlays expire so the scene never claims work/waiting forever.
    function tick() {
      var t = now();
      var changed = false;
      if (state.phase === 'interrupted' && t - phaseSince >= opts.interruptDwellMs) {
        changed = setPhase(state.mode === 'turns' ? 'idle' : 'listening') || changed;
      }
      for (var kind in state.work) {
        if (t - state.work[kind] >= opts.workTtlMs) {
          delete state.work[kind];
          changed = true;
        }
      }
      if (state.action === 'staged' && t - actionSince >= opts.actionTtlMs) {
        state.action = 'none';
        changed = true;
      }
      if (state.guard !== 'none' && t - guardSince >= opts.guardTtlMs) {
        state.guard = 'none';
        changed = true;
      }
      if (changed) emit();
      return changed;
    }

    function subscribe(listener) {
      listeners.push(listener);
      return function () {
        var at = listeners.indexOf(listener);
        if (at >= 0) listeners.splice(at, 1);
      };
    }

    return {
      handleEvent: handleEvent,
      setEnergy: setEnergy,
      tick: tick,
      getState: snapshot,
      subscribe: subscribe,
    };
  }

  // One plain-language label per state, overlay-aware. The live lane's
  // status banner reads from here so the words and the pose can never
  // disagree. (The Start/Done fallback keeps its own longer coaching
  // lines — a different lane with different mechanics.)
  function describe(state) {
    if (state.guard === 'redirect') return 'That one is for your doctor or family.';
    if (state.action === 'staged') {
      if (state.phase === 'talking') return 'Parker is talking — an action is on the screen to confirm.';
      return 'Waiting for you to confirm on the screen. Nothing has happened yet.';
    }
    var working = state.work.indexOf('search') >= 0;
    switch (state.phase) {
      case 'offline': return 'Live conversation is not available right now.';
      case 'idle': return 'Ready when you are.';
      case 'connecting': return 'Getting the line ready…';
      case 'listening': return working ? 'Listening — still checking that for you.' : 'Listening — just talk, any time.';
      case 'hearing': return 'Hearing you…';
      case 'thinking': return working ? 'Checking that for you…' : 'Thinking…';
      case 'talking': return 'Parker is talking — talk over it any time.';
      case 'interrupted': return 'Go ahead — listening.';
      case 'closing': return 'Wrapping up. Start again any time.';
      case 'stopped': return 'Stopped. Nothing else will happen until you start again.';
      case 'error': return 'The line hit a snag. Tap to try again.';
      default: return '';
    }
  }

  return {
    createController: createController,
    describe: describe,
    PHASES: PHASES.slice(),
    DEFAULTS: Object.assign({}, DEFAULTS),
  };
});
