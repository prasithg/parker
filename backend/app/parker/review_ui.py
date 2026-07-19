"""Single-file caregiver review page.

Served at GET /parker/review/ui. Plain HTML + vanilla JS over the local
/parker and /escalations APIs — no build step, no external assets, nothing
leaves the machine. Every button maps to an existing reviewed endpoint:
confirm/cancel staged actions, cancel queued outbox messages, acknowledge
or resolve escalations. There is deliberately no "send" button anywhere.
"""

REVIEW_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parker — caregiver review</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; margin: 2rem auto; max-width: 880px; padding: 0 1rem; color: #1a1a2e; }
  h1 { font-size: 1.4rem; } h2 { font-size: 1.05rem; margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: .3rem; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: .8rem 1rem; margin: .6rem 0; }
  .meta { color: #666; font-size: .85rem; margin-top: .25rem; }
  .empty { color: #888; font-style: italic; }
  button { margin-right: .5rem; margin-top: .5rem; padding: .35rem .8rem; border-radius: 6px; border: 1px solid #888; background: #f6f6f6; cursor: pointer; }
  button.primary { background: #2e6b2e; color: white; border-color: #2e6b2e; }
  button.danger { background: #fff; color: #a33; border-color: #a33; }
  .badge { display: inline-block; font-size: .75rem; padding: .1rem .5rem; border-radius: 10px; background: #eee; margin-left: .5rem; }
  .badge.staged, .badge.queued_local { background: #fff3cd; }
  .badge.confirmed, .badge.approved_local, .badge.released_local { background: #d4edda; }
  .badge.executed { background: #e2e3e5; }
  .badge.failed { background: #f8d7da; color: #842029; }
  .badge.cancelled { background: #f0e6e6; color: #844; }
  .badge.info { background: #d6e4f0; }
  .badge.warning, .badge.urgent { background: #f8d7da; }
  .note { background: #f4f7f4; border-radius: 8px; padding: .6rem 1rem; font-size: .85rem; }
  .safety { background: #fff8e8; border-left: 4px solid #c98500; border-radius: 8px; padding: .7rem 1rem; font-size: .86rem; margin: .8rem 0 1rem; }
  .safety strong { display: block; margin-bottom: .35rem; }
  .safety ul { margin: .25rem 0 0 1.1rem; padding: 0; }
  .safety li { margin: .18rem 0; }
  .updated { color: #888; font-size: .8rem; }
</style>
</head>
<body>
<h1>Parker — caregiver review</h1>
<p class="note">Everything here is local. Confirming queues a message to the local outbox and
approving marks it reviewed; nothing is ever sent externally from this page.</p>
<p class="note">Rearview mirror: <a href="/parker/digest">family digest — what happened, what
needs a look, what stayed local</a> (a local artifact, never sent; also <code>make digest</code>).</p>
<section class="safety" aria-label="Demo safety contract">
  <strong>Demo safety contract</strong>
  <ul>
    <li>Messages to family contacts the admin enabled release on the patient's own confirmation —
        this page shows them (rearview mirror), it does not gate them.</li>
    <li>Messages to anyone else still wait here for your per-message approval — the edge-case gate.</li>
    <li>No outbound sends exist in v0; released and approved messages alike remain on this machine.</li>
    <li>No medical advice, medication changes, purchases, or emergency-service replacement.</li>
    <li>No private credentials or sensitive notes are displayed or sent.</li>
    <li>Research handoffs are local read-only cards. No live fetch, purchase, submission, account change, or external message.</li>
    <li>Non-response items are candidates for review only — no notifications are dispatched here.</li>
  </ul>
</section>
<p class="updated" id="updated"></p>

<h2 id="h-pending">Pending actions (awaiting confirmation or execution)</h2>
<div id="pending"></div>

<h2 id="h-outbox">Outbox — awaiting your approval, never sent</h2>
<div id="outbox"></div>

<h2 id="h-released">Released to family contacts — patient-confirmed, still local only</h2>
<div id="released"></div>

<h2 id="h-approved">Approved — reviewed by you, still local only</h2>
<div id="approved"></div>

<h2 id="h-candidates">Non-response escalation candidates</h2>
<div id="candidates"></div>

<h2 id="h-escalations">Other open escalations</h2>
<div id="escalations"></div>

<h2 id="h-research-handoffs">Research handoffs — user-confirmed, local and read-only</h2>
<div id="research-handoffs"></div>

<h2 id="h-history">Recently done (stayed on this machine)</h2>
<div id="history"></div>

<h2 id="h-failed">Needs attention — skill failures (never retried automatically)</h2>
<div id="failed"></div>

<h2 id="h-exercise-sessions">Exercise sessions</h2>
<div id="exercise-sessions"></div>

<h2 id="h-evening-sessions">Evening loop</h2>
<div id="evening-sessions"></div>

<h2 id="h-cancelled">Changed my mind (cancelled)</h2>
<div id="cancelled"></div>
<div id="cancelled-messages"></div>

<script>
async function post(url, body) {
  const res = await fetch(url, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body || {})});
  if (!res.ok) alert('Request failed: ' + res.status);
  load();
}

function el(html) { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; }

function actionCard(a) {
  const what = a.action_type === 'family_message'
    ? `Message to <b>${a.recipient ?? '(no recipient)'}</b>: “${a.message_text ?? ''}”`
    : a.action_type === 'exercise_start'
      ? `Exercise: <b>${a.subject ?? ''}</b>`
      : `Reminder: <b>${a.subject ?? ''}</b>`;
  const card = el(`<div class="card">${what}<span class="badge ${a.status}">${a.status}</span>
    <div class="meta">resurfaced ${a.resurface_count}x · due ${a.execute_after ?? 'now'} · confirmed by ${a.confirmed_by ?? '—'}</div></div>`);
  if (a.status === 'staged') {
    const b = el('<button class="primary">Confirm (caregiver)</button>');
    b.onclick = () => post(`/parker/actions/${a.id}/confirm`, {confirmed_by: 'caregiver'});
    card.appendChild(b);
  }
  if (a.status === 'confirmed') {
    const b = el('<button class="primary">Execute (stays local)</button>');
    b.onclick = () => post(`/parker/actions/${a.id}/execute`);
    card.appendChild(b);
  }
  const c = el('<button class="danger">Cancel</button>');
  c.onclick = () => post(`/parker/actions/${a.id}/cancel`, {cancelled_by: 'caregiver'});
  card.appendChild(c);
  return card;
}

function outboxCard(m) {
  const approvedMeta = m.approved_at ? ` · approved by ${m.approved_by} at ${m.approved_at}` : '';
  const releasedMeta = m.released_at ? ` · released ${m.released_at} by ${m.released_by}` : '';
  const card = el(`<div class="card">To <b>${m.recipient}</b>: “${m.body}”<span class="badge ${m.status}">${m.status}</span>
    <div class="meta">queued ${m.created_at}${approvedMeta}${releasedMeta}</div></div>`);
  if (m.status === 'queued_local') {
    const a = el('<button class="primary">Approve (stays local)</button>');
    a.onclick = () => post(`/parker/outbox/${m.id}/approve`, {approved_by: 'caregiver'});
    card.appendChild(a);
  }
  const c = el('<button class="danger">Cancel message</button>');
  c.onclick = () => post(`/parker/outbox/${m.id}/cancel`);
  card.appendChild(c);
  return card;
}

function escalationCard(e) {
  const card = el(`<div class="card">${e.reason}<span class="badge ${e.severity}">${e.severity}</span>
    <div class="meta">status ${e.status} · created ${e.created_at}</div></div>`);
  if (!e.acknowledged_at) {
    const a = el('<button>Acknowledge</button>');
    a.onclick = () => post(`/escalations/${e.id}/acknowledge`);
    card.appendChild(a);
  }
  const r = el('<button>Resolve</button>');
  r.onclick = () => post(`/escalations/${e.id}/resolve`, {notes: 'resolved from review page'});
  card.appendChild(r);
  return card;
}

function historyCard(a) {
  const what = a.action_type === 'family_message'
    ? `Message to <b>${a.recipient ?? '(no recipient)'}</b>: “${a.message_text ?? ''}” — queued to local outbox`
    : a.action_type === 'exercise_start'
      ? `Exercise started: <b>${a.subject ?? ''}</b>`
      : `Reminder: <b>${a.subject ?? ''}</b>`;
  return el(`<div class="card">${what}<span class="badge executed">executed</span>
    <div class="meta">done ${a.executed_at ?? '—'} · confirmed by ${a.confirmed_by ?? '—'} · ${a.execution_result ?? ''}</div></div>`);
}

function researchHandoffCard(h) {
  const card = document.createElement('div');
  card.className = 'card';
  const query = document.createElement('b');
  query.textContent = h.query;
  card.appendChild(query);
  const badge = document.createElement('span');
  badge.className = `badge ${h.status}`;
  badge.textContent = h.status;
  card.appendChild(badge);

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = `interpretation: ${h.selected_interpretation} · provenance: ${h.provenance_status} · risk: ${h.risk_label}`;
  card.appendChild(meta);

  if (h.status === 'ready') {
    const done = el('<button class="primary">Mark research complete</button>');
    done.onclick = () => post(`/parker/research-handoffs/${h.id}/complete`, {completed_by: 'caregiver'});
    card.appendChild(done);
    const cancel = el('<button class="danger">Cancel research card</button>');
    cancel.onclick = () => post(`/parker/research-handoffs/${h.id}/cancel`, {cancelled_by: 'caregiver'});
    card.appendChild(cancel);
  }
  return card;
}

function exerciseSessionCard(s) {
  const card = document.createElement('div');
  card.className = 'card';
  const title = document.createElement('b');
  title.textContent = s.subject;
  card.appendChild(title);
  const badge = document.createElement('span');
  badge.className = `badge ${s.status}`;
  badge.textContent = s.status;
  card.appendChild(badge);

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.appendChild(document.createTextNode(`${s.category} · ${s.difficulty} · started ${s.started_at ?? '—'}`));
  if (s.caregiver_note) {
    meta.appendChild(document.createTextNode(' · note: '));
    const noteText = document.createElement('span');
    noteText.textContent = s.caregiver_note;
    meta.appendChild(noteText);
  }
  card.appendChild(meta);

  const prompt = document.createElement('div');
  prompt.className = 'meta';
  prompt.appendChild(document.createTextNode('Prompt card: '));
  const promptText = document.createElement('span');
  promptText.textContent = s.prompt_card;
  prompt.appendChild(promptText);
  card.appendChild(prompt);

  if (s.status === 'started') {
    const done = el('<button class="primary">Mark complete</button>');
    done.onclick = () => post(`/parker/exercises/${s.id}/complete`, {caregiver_note: 'completed from review page'});
    card.appendChild(done);
    const cancel = el('<button class="danger">Cancel exercise</button>');
    cancel.onclick = () => post(`/parker/exercises/${s.id}/cancel`, {caregiver_note: 'cancelled from review page'});
    card.appendChild(cancel);
  }
  return card;
}

function eveningSessionCard(s) {
  const card = document.createElement('div');
  card.className = 'card';
  const title = document.createElement('b');
  title.textContent = 'Recliner/TV check-in';
  card.appendChild(title);
  const badge = document.createElement('span');
  badge.className = `badge ${s.status}`;
  badge.textContent = s.status;
  card.appendChild(badge);

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.appendChild(document.createTextNode(`${s.evening_date} · started ${s.started_at ?? '—'}`));
  if (s.caregiver_note) {
    meta.appendChild(document.createTextNode(' · note: '));
    const noteText = document.createElement('span');
    noteText.textContent = s.caregiver_note;
    meta.appendChild(noteText);
  }
  card.appendChild(meta);

  const prompt = document.createElement('div');
  prompt.className = 'meta';
  prompt.appendChild(document.createTextNode('Prompt card: '));
  const promptText = document.createElement('span');
  promptText.textContent = s.prompt_card;
  prompt.appendChild(promptText);
  card.appendChild(prompt);

  if (['offered', 'engaged', 'timed_out'].includes(s.status)) {
    const done = el('<button class="primary">Mark complete</button>');
    done.onclick = () => post(`/parker/evening/${s.id}/complete`, {caregiver_note: 'completed from review page'});
    card.appendChild(done);
    const cancel = el('<button class="danger">Cancel evening loop</button>');
    cancel.onclick = () => post(`/parker/evening/${s.id}/cancel`, {caregiver_note: 'cancelled from review page'});
    card.appendChild(cancel);
  }
  return card;
}

function failedActionCard(a) {
  return el(`<div class="card">${a.action_type}: <b>${a.subject ?? ''}</b><span class="badge failed">failed</span>
    <div class="meta">confirmed by ${a.confirmed_by ?? '—'} · ${a.execution_result ?? ''}</div></div>`);
}

function cancelledActionCard(a) {
  const what = a.action_type === 'family_message'
    ? `Message to <b>${a.recipient ?? '(no recipient)'}</b>: “${a.message_text ?? ''}”`
    : `Reminder: <b>${a.subject ?? ''}</b>`;
  return el(`<div class="card">${what}<span class="badge cancelled">cancelled</span>
    <div class="meta">cancelled ${a.cancelled_at ?? '—'} by ${a.cancelled_by ?? '—'}</div></div>`);
}

function cancelledMessageCard(m) {
  return el(`<div class="card">To <b>${m.recipient}</b>: “${m.body}”<span class="badge cancelled">message cancelled</span>
    <div class="meta">queued ${m.created_at} · cancelled ${m.cancelled_at ?? '—'}</div></div>`);
}

function fill(id, items, builder, emptyText, headerBase) {
  const root = document.getElementById(id);
  root.innerHTML = '';
  const header = document.getElementById('h-' + id);
  if (header && headerBase) header.textContent = `${headerBase} (${items.length})`;
  if (!items.length) { root.appendChild(el(`<p class="empty">${emptyText}</p>`)); return; }
  items.forEach(item => root.appendChild(builder(item)));
}

async function load() {
  const data = await (await fetch('/parker/review')).json();
  fill('pending', data.pending_actions, actionCard, 'Nothing waiting.', 'Pending actions');
  fill('outbox', data.outbox_queued, outboxCard, 'Outbox is empty.', 'Outbox — awaiting your approval, never sent');
  fill('released', data.outbox_released, outboxCard, 'Nothing released by capability policy yet.', 'Released to family contacts — patient-confirmed, still local only');
  fill('approved', data.outbox_approved, outboxCard, 'Nothing approved yet.', 'Approved — reviewed by you, still local only');
  fill('candidates', data.escalation_candidates, escalationCard, 'No non-response candidates.', 'Non-response escalation candidates');
  fill('escalations', data.open_escalations, escalationCard, 'No open escalations.', 'Other open escalations');
  fill('research-handoffs', data.research_handoffs, researchHandoffCard, 'No research handoffs.', 'Research handoffs — user-confirmed, local and read-only');
  fill('history', data.recent_history, historyCard, 'Nothing executed yet.', 'Recently done (stayed on this machine)');
  fill('failed', data.recent_failed, failedActionCard, 'No skill failures.', 'Needs attention — skill failures (never retried automatically)');
  fill('exercise-sessions', data.recent_exercise_sessions, exerciseSessionCard, 'No exercise sessions yet.', 'Exercise sessions');
  fill('evening-sessions', data.recent_evening_sessions, eveningSessionCard, 'No evening-loop sessions yet.', 'Evening loop');
  const cancelledTotal = data.recent_cancelled.length + data.outbox_cancelled.length;
  fill('cancelled', data.recent_cancelled, cancelledActionCard, cancelledTotal ? '' : 'Nothing cancelled.', null);
  fill('cancelled-messages', data.outbox_cancelled, cancelledMessageCard, '', null);
  document.getElementById('h-cancelled').textContent = `Changed my mind (cancelled) (${cancelledTotal})`;
  document.getElementById('updated').textContent = 'Last updated ' + new Date().toLocaleTimeString();
}
load();
setInterval(load, 15000);
</script>
</body>
</html>
"""
