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
  .badge.confirmed, .badge.approved_local { background: #d4edda; }
  .badge.executed { background: #e2e3e5; }
  .badge.cancelled { background: #f0e6e6; color: #844; }
  .badge.info { background: #d6e4f0; }
  .badge.warning, .badge.urgent { background: #f8d7da; }
  .note { background: #f4f7f4; border-radius: 8px; padding: .6rem 1rem; font-size: .85rem; }
  .updated { color: #888; font-size: .8rem; }
</style>
</head>
<body>
<h1>Parker — caregiver review</h1>
<p class="note">Everything here is local. Confirming queues a message to the local outbox and
approving marks it reviewed; nothing is ever sent externally from this page.</p>
<p class="updated" id="updated"></p>

<h2 id="h-pending">Pending actions (awaiting confirmation or execution)</h2>
<div id="pending"></div>

<h2 id="h-outbox">Outbox — awaiting your approval, never sent</h2>
<div id="outbox"></div>

<h2 id="h-approved">Approved — reviewed by you, still local only</h2>
<div id="approved"></div>

<h2 id="h-candidates">Non-response escalation candidates</h2>
<div id="candidates"></div>

<h2 id="h-escalations">Other open escalations</h2>
<div id="escalations"></div>

<h2 id="h-history">Recently done (stayed on this machine)</h2>
<div id="history"></div>

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
  const card = el(`<div class="card">To <b>${m.recipient}</b>: “${m.body}”<span class="badge ${m.status}">${m.status}</span>
    <div class="meta">queued ${m.created_at}${approvedMeta}</div></div>`);
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
    : `Reminder: <b>${a.subject ?? ''}</b>`;
  return el(`<div class="card">${what}<span class="badge executed">executed</span>
    <div class="meta">done ${a.executed_at ?? '—'} · confirmed by ${a.confirmed_by ?? '—'} · ${a.execution_result ?? ''}</div></div>`);
}

function cancelledActionCard(a) {
  const what = a.action_type === 'family_message'
    ? `Message to <b>${a.recipient ?? '(no recipient)'}</b>: “${a.message_text ?? ''}”`
    : `Reminder: <b>${a.subject ?? ''}</b>`;
  return el(`<div class="card">${what}<span class="badge cancelled">cancelled</span>
    <div class="meta">${a.execution_result ?? ''}</div></div>`);
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
  fill('approved', data.outbox_approved, outboxCard, 'Nothing approved yet.', 'Approved — reviewed by you, still local only');
  fill('candidates', data.escalation_candidates, escalationCard, 'No non-response candidates.', 'Non-response escalation candidates');
  fill('escalations', data.open_escalations, escalationCard, 'No open escalations.', 'Other open escalations');
  fill('history', data.recent_history, historyCard, 'Nothing executed yet.', 'Recently done (stayed on this machine)');
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
