# Parker local v0 — demo runbook

A scripted walkthrough of everything Parker v0 can do locally, end to end, with no external services, no API keys, and no real sends. Written 2026-06-09; updated 2026-06-10 (reset-db, review UI, text loop).

## Prerequisites

```bash
make backend-venv    # Python 3.11 venv + deps
make test            # full suite should pass
make reset-db        # deterministic fresh local DB (v0 schema changes need this)
```

Start the server:

```bash
make run    # uvicorn on http://localhost:8000, DB at backend/parkinsclaw.db
curl -s localhost:8000/health
```

`make run` and all seeding/REPL commands below share `backend/parkinsclaw.db`. All `/parker` endpoints accept an optional `now` so the demo is deterministic; omit it to use real time.

## Fastest path: `make demo`

One command resets the DB, seeds a believable family day through the real pipeline, and replays a synthetic effortful-speech transcript through the text loop:

```bash
make demo
make run    # then open http://localhost:8000/parker/review/ui
```

The review page opens populated: three actions awaiting confirmation (two reminders, one drafted message to Rohan with its text restated), one message to Sarah queued in the local outbox (cancel it!), and one non-response escalation candidate from a reminder that was resurfaced three times without an answer. The printed replay dialogue shows repair choices, a refused medication question, and a purchase routed to human approval.

## Demo 0 — Talk to Parker (text loop) + caregiver review page

For a live version of the same flow, use two terminals and no curl:

```bash
make repl    # terminal 1 — type utterances as the user
```

Try, in order:

- `Remind me to water the plants` → captured as a pending reminder.
- `Tell Sarah dinner on Sunday would be lovely` → captured as a family message draft.
- `Call... the... you know... the one with the garden...` → Parker offers numbered repair choices; answer `1`.
- `Should I take half my pills?` → refused, redirected to doctor/family.
- `Order that walker with the card on file` → routed to human approval, nothing captured.

Then open the caregiver view (terminal 2 running `make run`):

```text
http://localhost:8000/parker/review/ui
```

Run a tick so captured intents stage (`curl -s -X POST localhost:8000/parker/tick -H 'content-type: application/json' -d '{}'`), refresh the page, and use the buttons: confirm/cancel pending actions, execute (stays local), cancel queued outbox messages, acknowledge escalation candidates. There is no send button anywhere by design.

## Demo 1 — Reminder: capture → tick → resurface → confirm → execute

Capture normally happens via the `capture_intent` conversation tool; for the demo, insert one through the API-equivalent flow by ticking a pre-seeded intent, or exercise the tool layer from a Python shell:

```bash
cd backend && ./.venv/bin/python -c "
from app.db.database import SessionLocal, create_tables
from app.db.models import CallLog
from app.parker.pipeline import capture_intent
create_tables()
db = SessionLocal()
call = CallLog(call_sid='DEMO-1', call_type='check_in'); db.add(call); db.commit()
capture_intent(db, call_log_id=call.id, intent_text='Remind me to water the plants', requested_action='remind', subject='water the plants', due_at='2026-06-09T09:00:00')
print('captured')
"
```

Then drive the loop over HTTP:

```bash
curl -s -X POST localhost:8000/parker/tick -H 'content-type: application/json' \
  -d '{"now": "2026-06-09T09:00:00"}'
# {"resolved": 1, "staged": 1, "escalation_candidates": 0}

curl -s 'localhost:8000/parker/resurface?now=2026-06-09T09:00:01'
# action shows subject "water the plants"; note the action id

curl -s -X POST localhost:8000/parker/actions/1/confirm -H 'content-type: application/json' \
  -d '{"confirmed_by": "patient"}'

curl -s -X POST localhost:8000/parker/actions/1/execute -H 'content-type: application/json' -d '{}'
# status "executed", execution_result "reminder resurfaced: water the plants"
```

Try executing *before* confirming to see the gate: status comes back `blocked` with "requires confirmation".

## Demo 2 — Family message: confirm → local outbox → cancel

Capture a message intent (recipient is a contact *name*, never a number):

```bash
cd backend && ./.venv/bin/python -c "
from app.db.database import SessionLocal
from app.db.models import CallLog
from app.parker.pipeline import capture_intent
db = SessionLocal()
call = CallLog(call_sid='DEMO-2', call_type='check_in'); db.add(call); db.commit()
capture_intent(db, call_log_id=call.id, intent_text='Dinner Sunday?', requested_action='message', recipient='Sarah', due_at='2026-06-09T10:00:00')
print('captured')
"
```

```bash
curl -s -X POST localhost:8000/parker/tick -H 'content-type: application/json' -d '{"now": "2026-06-09T10:00:00"}'
curl -s 'localhost:8000/parker/resurface?now=2026-06-09T10:00:01'
# confirmation card restates exactly what will happen:
#   "recipient": "Sarah", "message_text": "Dinner Sunday?"

curl -s -X POST localhost:8000/parker/actions/<id>/confirm -H 'content-type: application/json' -d '{"confirmed_by": "patient"}'
curl -s -X POST localhost:8000/parker/actions/<id>/execute -H 'content-type: application/json' -d '{}'
# "family message queued locally for Sarah (outbox 1)"

curl -s localhost:8000/parker/outbox
# the queued_local row — this is as far as a message can possibly go in v0

curl -s -X POST localhost:8000/parker/outbox/1/cancel
# status "cancelled" — the reversibility story
```

## Demo 3 — Non-response → escalation candidate

Resurface a staged reminder three times without confirming (repeat the resurface call with advancing `now`), then tick past the quiet window:

```bash
curl -s 'localhost:8000/parker/resurface?now=2026-06-09T11:00:00' >/dev/null
curl -s 'localhost:8000/parker/resurface?now=2026-06-09T11:20:00' >/dev/null
curl -s 'localhost:8000/parker/resurface?now=2026-06-09T11:40:00' >/dev/null

curl -s -X POST localhost:8000/parker/tick -H 'content-type: application/json' \
  -d '{"now": "2026-06-09T12:30:00"}'
# {"resolved": 0, "staged": 0, "escalation_candidates": 1}

curl -s localhost:8000/escalations/   # open info-severity candidate, nobody notified
```

Thresholds: `PARKER_NON_RESPONSE_RESURFACE_THRESHOLD` (default 3) and `PARKER_NON_RESPONSE_QUIET_MINUTES` (default 30) via environment.

## Demo 4 — Repair choices (tool layer)

```bash
cd backend && ./.venv/bin/python -c "
from app.db.database import SessionLocal
from app.db.models import CallLog
from app.conversation.tools import execute_tool
db = SessionLocal()
call = CallLog(call_sid='DEMO-4', call_type='check_in'); db.add(call); db.commit()
result = execute_tool(db, call.id, 'offer_repair_choices', {'candidates': [
    {'label': 'call your neighbor Mary', 'action_type': 'family_message'},
    {'label': 'remind you to call Mary', 'action_type': 'reminder'},
]})
print(result['spoken_prompt'])
"
# Did you mean: 1) call your neighbor Mary, 2) remind you to call Mary, or 3) none of these?
```

Offer an unsafe candidate (`action_type: medication_change`) to see the typed rejection.

## Demo 5 — Evals

```bash
make eval-tasks                                    # task-taxonomy eval, rule-based baseline
python3 benchmark/evaluate_tasks_v0.py --write-report   # benchmark/reports/
cd backend && ./.venv/bin/pytest -q                # full suite
```

## What this demo deliberately cannot show

- No real calls, SMS, or any outbound delivery — the outbox has no sender.
- No purchases, smart-home, or calendar writes — policy-blocked (`human_operator`).
- No medical advice or medication changes — policy-refused, never confirmable.
- No voice cloning — optional, consent-gated, not part of v0.
- Family-wide escalation notifications — candidates are review-only (`info`, undispatched).
