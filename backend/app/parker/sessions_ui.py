"""Single-file review-the-session page (the human-testing flywheel).

Served at GET /parker/sessions/ui, behind the same opt-in dashboard auth
as the caregiver review page. Plain HTML + vanilla JS over the local
/parker/sessions APIs — no build step, no external assets, nothing
leaves the machine. It shows a finished live session back to the human
tester — every turn, worker injection, lookup ack, proposal, and guard
trip with its latencies, plus what the NEXT session's context card now
carries — and files one-tap "that felt wrong because…" feedback against
a specific event. Transcript and feedback text always render through
textContent, never innerHTML interpolation.
"""

SESSIONS_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parker — session review</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; margin: 2rem auto; max-width: 880px; padding: 0 1rem; color: #1a1a2e; }
  h1 { font-size: 1.4rem; } h2 { font-size: 1.05rem; margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: .3rem; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: .8rem 1rem; margin: .6rem 0; }
  .card.session { cursor: pointer; }
  .card.session:hover { border-color: #2e6b2e; }
  .card.selected { border-color: #2e6b2e; box-shadow: 0 0 0 1px #2e6b2e; }
  .meta { color: #666; font-size: .85rem; margin-top: .25rem; }
  .empty { color: #888; font-style: italic; }
  button { margin-right: .5rem; margin-top: .5rem; padding: .35rem .8rem; border-radius: 6px; border: 1px solid #888; background: #f6f6f6; cursor: pointer; }
  button.primary { background: #2e6b2e; color: white; border-color: #2e6b2e; }
  button.danger { background: #fff; color: #a33; border-color: #a33; }
  .badge { display: inline-block; font-size: .75rem; padding: .1rem .5rem; border-radius: 10px; background: #eee; margin-left: .5rem; }
  .badge.turn { background: #d6e4f0; }
  .badge.injection { background: #e7dff0; }
  .badge.lookup_ack { background: #fff3cd; }
  .badge.proposal { background: #d4edda; }
  .badge.guard_trip { background: #f8d7da; color: #842029; }
  .badge.live { background: #fff3cd; }
  .badge.staged { background: #fff3cd; }
  .badge.confirmed { background: #d4edda; }
  .badge.executed { background: #e2e3e5; }
  .badge.failed { background: #f8d7da; color: #842029; }
  .badge.cancelled, .badge.blocked { background: #f0e6e6; color: #844; }
  .chip { display: inline-block; font-size: .72rem; padding: .05rem .45rem; border-radius: 8px; background: #f0f0f0; color: #555; margin-right: .35rem; margin-top: .3rem; }
  .who { color: #666; font-size: .8rem; }
  .speech { margin: .15rem 0 .35rem; white-space: pre-wrap; }
  .injected { background: #f7f5fb; border-radius: 6px; padding: .4rem .6rem; font-size: .88rem; white-space: pre-wrap; }
  .caught { background: #fdf1f1; border-radius: 6px; padding: .4rem .6rem; font-size: .88rem; white-space: pre-wrap; }
  .note { background: #f4f7f4; border-radius: 8px; padding: .6rem 1rem; font-size: .85rem; }
  .feedback { background: #fdf6ec; border-left: 3px solid #c98500; border-radius: 6px; padding: .35rem .6rem; font-size: .85rem; margin-top: .4rem; white-space: pre-wrap; }
  .cardline { margin: .12rem 0; }
  .cardline.minted { background: #eaf6ea; border-radius: 4px; padding: .05rem .3rem; }
  textarea { width: 100%; box-sizing: border-box; min-height: 3.2rem; margin-top: .4rem; font: inherit; padding: .4rem; border-radius: 6px; border: 1px solid #aaa; }
  .updated { color: #888; font-size: .8rem; }
</style>
</head>
<body>
<h1>Parker — session review</h1>
<p class="note">The human-testing flywheel: pick a finished live conversation and judge it —
what Parker heard, said, injected, and staged, with ack and inject latencies, and what
tomorrow's context card now carries. Everything here is local; feedback stays on this
machine. Tap “Felt wrong…” on any moment to file why.</p>
<p class="updated" id="updated"></p>

<h2>Recent live sessions</h2>
<div id="sessions"></div>

<div id="detail" hidden>
  <h2 id="detail-title">Session</h2>
  <p class="meta" id="detail-meta"></p>
  <div id="timeline"></div>

  <h2>Staged from this session</h2>
  <div id="staged"></div>

  <h2>What tomorrow's card now carries</h2>
  <p class="meta">Computed right now from the same builder the next session will use
  (ambient gateway lines are only probed live, in-session, so they are absent here).
  Medication and streak lines follow the clock, so tomorrow's actual card can differ.</p>
  <div id="next-card"></div>
</div>

<script>
'use strict';
const $ = id => document.getElementById(id);
let selectedSid = null;

function el(html) { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; }

function text(node, value) { node.textContent = value == null ? '' : String(value); return node; }

function when(iso) {
  if (!iso) return '—';
  return new Date(iso + 'Z').toLocaleString();
}

async function load() {
  const res = await fetch('/parker/sessions');
  if (!res.ok) { $('updated').textContent = 'Failed to load sessions: ' + res.status; return; }
  const data = await res.json();
  const box = $('sessions');
  box.replaceChildren();
  if (!data.sessions.length) {
    box.appendChild(el('<div class="empty">No live sessions yet — have a conversation on the Live line first.</div>'));
  }
  for (const s of data.sessions) {
    const card = el(`<div class="card session">
      <b class="when"></b>${s.live ? '<span class="badge live">live</span>' : ''}
      <span class="chip">${s.turn_count} turn(s)</span>
      <span class="chip">${s.feedback_count} feedback</span>
      <span class="chip">${s.duration_seconds != null ? s.duration_seconds + 's' : '—'}</span>
      <div class="meta summary"></div></div>`);
    text(card.querySelector('.when'), when(s.started_at));
    text(card.querySelector('.summary'), s.summary || '(no summary yet)');
    if (s.call_sid === selectedSid) card.classList.add('selected');
    card.onclick = () => { selectedSid = s.call_sid; load(); loadDetail(s.call_sid); };
    box.appendChild(card);
  }
  $('updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

function feedbackControls(sid, ev, container) {
  for (const f of ev.feedback) {
    const row = el('<div class="feedback"></div>');
    text(row, 'Felt wrong: ' + (f.note || '(no note)'));
    container.appendChild(row);
  }
  const btn = el('<button class="danger">Felt wrong…</button>');
  btn.onclick = () => {
    btn.hidden = true;
    const area = el('<div><textarea placeholder="because…"></textarea></div>');
    const save = el('<button class="primary">File it</button>');
    const cancel = el('<button>Never mind</button>');
    save.onclick = async () => {
      const res = await fetch(`/parker/sessions/${encodeURIComponent(sid)}/feedback`, {
        method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({event_id: ev.id, note: area.querySelector('textarea').value})
      });
      if (!res.ok) alert('Could not file feedback: ' + res.status);
      loadDetail(sid);
    };
    cancel.onclick = () => { area.remove(); save.remove(); cancel.remove(); btn.hidden = false; };
    container.appendChild(area); container.appendChild(save); container.appendChild(cancel);
  };
  container.appendChild(btn);
}

function eventCard(sid, ev) {
  const card = el(`<div class="card"><span class="badge ${ev.kind}">${ev.kind.replace('_', ' ')}</span>
    <span class="chip">#${ev.seq}</span><span class="chip">${(ev.detail.t_ms / 1000).toFixed(1)}s in</span>
    <div class="body"></div></div>`);
  const body = card.querySelector('.body');
  const d = ev.detail;
  if (ev.kind === 'turn') {
    if (ev.heard) {
      body.appendChild(el('<div class="who">He said</div>'));
      body.appendChild(text(el('<div class="speech"></div>'), ev.heard));
    }
    if (ev.said) {
      body.appendChild(el('<div class="who">Parker said</div>'));
      body.appendChild(text(el('<div class="speech"></div>'), ev.said));
    }
    if (!ev.said) body.appendChild(el('<div class="meta">Parker never answered this one' + (d.dangling ? ' (captured at hang-up)' : '') + '</div>'));
    if (d.guard_tripped) body.appendChild(el('<span class="badge guard_trip">medical guard spoke the redirect</span>'));
  } else if (ev.kind === 'guard_trip') {
    body.appendChild(el('<div class="who">The guard cancelled this mid-word</div>'));
    body.appendChild(text(el('<div class="caught"></div>'), ev.said));
  } else if (ev.kind === 'lookup_ack') {
    body.appendChild(el('<div class="who">He asked Parker to look up</div>'));
    body.appendChild(text(el('<div class="speech"></div>'), d.question || ''));
    body.appendChild(el(`<div><span class="chip">ack: ${d.status}</span><span class="chip">acked in ${d.ack_ms}ms</span></div>`));
  } else if (ev.kind === 'injection') {
    body.appendChild(el(`<div class="who">${d.worker === 'context' ? 'Context card handed to the model' : 'Lookup result injected'}</div>`));
    if (d.question) body.appendChild(text(el('<div class="meta"></div>'), 'For: ' + d.question));
    body.appendChild(text(el('<div class="injected"></div>'), ev.said));
    const chips = el('<div></div>');
    chips.appendChild(el(`<span class="chip">worker ${d.worker_ms}ms</span>`));
    if (d.since_ask_ms != null) chips.appendChild(el(`<span class="chip">asked → injected ${(d.since_ask_ms / 1000).toFixed(1)}s</span>`));
    if (d.age_s != null) chips.appendChild(el(`<span class="chip">age ${d.age_s}s</span>`));
    if (d.sources) chips.appendChild(el(`<span class="chip">${d.sources} source(s)</span>`));
    if (d.error) chips.appendChild(text(el('<span class="chip"></span>'), 'error: ' + d.error));
    body.appendChild(chips);
  } else if (ev.kind === 'proposal') {
    body.appendChild(el('<div class="who">Parker proposed</div>'));
    body.appendChild(text(el('<div class="speech"></div>'), (d.label || d.action_type || '')));
    const row = el(`<div><span class="badge ${d.status === 'staged' ? 'staged' : 'failed'}"></span><span class="chip"></span></div>`);
    text(row.querySelector('.badge'), d.status);
    text(row.querySelector('.chip'), d.action_type);  // model-controlled: textContent only
    body.appendChild(row);
    if (d.note) body.appendChild(text(el('<div class="meta"></div>'), d.note));
  }
  feedbackControls(sid, ev, body);
  return card;
}

async function loadDetail(sid) {
  const res = await fetch('/parker/sessions/' + encodeURIComponent(sid));
  if (!res.ok) { alert('Failed to load session: ' + res.status); return; }
  const s = await res.json();
  $('detail').hidden = false;
  text($('detail-title'), 'Session — ' + when(s.started_at));
  text($('detail-meta'),
    (s.live ? 'still live · ' : '') +
    (s.duration_seconds != null ? s.duration_seconds + 's · ' : '') + (s.summary || ''));
  const timeline = $('timeline');
  timeline.replaceChildren();
  if (!s.events.length) timeline.appendChild(el('<div class="empty">No journal for this session (it predates the flywheel, or nothing was said).</div>'));
  for (const ev of s.events) timeline.appendChild(eventCard(s.call_sid, ev));

  const staged = $('staged');
  staged.replaceChildren();
  if (!s.staged_actions.length) staged.appendChild(el('<div class="empty">Nothing was staged.</div>'));
  for (const a of s.staged_actions) {
    const card = el(`<div class="card"><span class="badge ${a.status}">${a.status}</span><span class="chip"></span><div class="meta summary"></div></div>`);
    text(card.querySelector('.chip'), a.action_type);
    text(card.querySelector('.summary'), a.summary);
    staged.appendChild(card);
  }

  const cardBox = $('next-card');
  cardBox.replaceChildren();
  if (!s.next_card.lines.length) cardBox.appendChild(el('<div class="empty">The next session would get no card at all.</div>'));
  for (const line of s.next_card.lines) {
    const row = text(el('<div class="cardline"></div>'), line);
    if (s.minted_memory && line.includes(s.minted_memory)) {
      row.classList.add('minted');
      row.appendChild(el('<span class="badge turn">this session put it there</span>'));
    }
    cardBox.appendChild(row);
  }
  if (s.minted_memory) {
    const minted = el('<p class="meta"></p>');
    text(minted, 'Topic memory minted by this session: ' + s.minted_memory);
    cardBox.appendChild(minted);
  }
}

load();
setInterval(load, 15000);
</script>
</body>
</html>
"""
