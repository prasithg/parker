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
  .note { background: #f4f7f4; border-radius: 8px; padding: .6rem 1rem; font-size: .85rem; }
</style>
</head>
<body>
<h1>Parker — caregiver review</h1>
<p class="note">Everything here is local. Confirming a message queues it to the local outbox only;
nothing is ever sent externally from this page.</p>

<h2>Pending actions (awaiting confirmation or execution)</h2>
<div id="pending"></div>

<h2>Outbox — queued locally, never sent</h2>
<div id="outbox"></div>

<h2>Non-response escalation candidates</h2>
<div id="candidates"></div>

<h2>Other open escalations</h2>
<div id="escalations"></div>

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
  const card = el(`<div class="card">${what}<span class="badge">${a.status}</span>
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
  const card = el(`<div class="card">To <b>${m.recipient}</b>: “${m.body}”<span class="badge">${m.status}</span>
    <div class="meta">queued ${m.created_at}</div></div>`);
  const c = el('<button class="danger">Cancel message</button>');
  c.onclick = () => post(`/parker/outbox/${m.id}/cancel`);
  card.appendChild(c);
  return card;
}

function escalationCard(e) {
  const card = el(`<div class="card">${e.reason}<span class="badge">${e.severity}</span>
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

function fill(id, items, builder, emptyText) {
  const root = document.getElementById(id);
  root.innerHTML = '';
  if (!items.length) { root.appendChild(el(`<p class="empty">${emptyText}</p>`)); return; }
  items.forEach(item => root.appendChild(builder(item)));
}

async function load() {
  const data = await (await fetch('/parker/review')).json();
  fill('pending', data.pending_actions, actionCard, 'Nothing waiting.');
  fill('outbox', data.outbox_queued, outboxCard, 'Outbox is empty.');
  fill('candidates', data.escalation_candidates, escalationCard, 'No non-response candidates.');
  fill('escalations', data.open_escalations, escalationCard, 'No open escalations.');
}
load();
setInterval(load, 15000);
</script>
</body>
</html>
"""
