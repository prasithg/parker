# Parker local v0 — demo runbook

A scripted walkthrough of everything Parker v0 can do locally, end to end, with no real sends. Written 2026-06-09; updated through 2026-06-10 (voice, auth, repair choices, continuous loop).

## Pilot setup: what to configure

Parker works zero-config for a local demo. For a real LAN pilot, do the following before starting.

**1. Copy and edit the env file:**

```bash
cp backend/.env.example backend/.env
# Edit backend/.env — at minimum set PATIENT_NAME
```

**2. Model-enhanced repair choices (strongly recommended):**

When Parker hears ambiguous effortful speech, it offers numbered repair choices. Without an API key the choices are generic ("set a reminder about this"). With one they are specific to what was actually said ("remind you to call your neighbour"):

```bash
# in backend/.env:
ANTHROPIC_API_KEY=sk-ant-...   # get at console.anthropic.com
```

Then spot-check quality before the pilot session:

```bash
ANTHROPIC_API_KEY=sk-ant-... make eval-repair
```

**3. Lock the caregiver review page (recommended):**

```bash
# in backend/.env:
DASHBOARD_PASSWORD=choose-a-passphrase
```

The browser prompts once; buttons on the page reuse the credentials automatically. `/parker/tick` and `/parker/resurface` remain open — they are the assistant-loop surface.

**4. Optional voice transcription:**

```bash
make voice-deps    # installs faster-whisper + sounddevice (local, no cloud)
```

Required for `make demo-voice`, `make talk`, and `make talk-loop`. macOS prompts for microphone permission on first use of `make talk` / `make talk-loop`.

## Prerequisites

```bash
make backend-venv    # Python 3.11 venv + deps
make test            # full suite should pass
make reset-db        # deterministic fresh local DB (v0 schema changes need this)
```

Start the server:

```bash
make run    # uvicorn on http://localhost:8000, DB at backend/parker.db
curl -s localhost:8000/health
```

`make run` and all seeding/REPL commands below share `backend/parker.db`. All `/parker` endpoints accept an optional `now` so the demo is deterministic; omit it to use real time.

## Fastest path: `make demo`

One command resets the DB, seeds a believable family day through the real pipeline, and replays a synthetic effortful-speech transcript through the text loop:

```bash
make demo
make run    # then open http://localhost:8000/parker/review/ui
```

The review page opens populated: three actions awaiting confirmation (two reminders, one drafted message to Rohan with its text restated), one message to Sarah queued in the local outbox (approve it — it moves to the "still local only" section — or cancel it), and one non-response escalation candidate from a reminder that was resurfaced three times without an answer. The printed replay dialogue shows repair choices, a refused medication question, and a purchase routed to human approval.

Message lifecycle on the page: patient confirms → `queued_local` (awaiting your approval) → `approved_local` (reviewed, still on this machine) — or cancelled from either state. No send exists.

### Review-page auth (opt-in)

With no password configured (the default), everything above works credential-free on localhost. To lock the caregiver decision surface — review feed/page, outbox, and the confirm/execute/cancel/approve buttons — set credentials before `make run`:

```bash
DASHBOARD_USERNAME=family DASHBOARD_PASSWORD=choose-one make run
```

The browser prompts once for HTTP Basic sign-in; the page's buttons reuse the credentials automatically, and curl flows need `-u family:choose-one`. `/parker/tick` and `/parker/resurface` deliberately stay open — they're the assistant-loop surface, not the caregiver's.

## Voice path: `make demo-voice` (optional, local-only)

The transcript seam accepts real speech. Install the optional on-device transcriber (faster-whisper; first run downloads model weights to the local Hugging Face cache, then inference is fully offline — no cloud speech APIs):

```bash
make voice-deps
make demo-voice AUDIO=path/to/recording.wav
```

The audio is transcribed on this machine, split into one utterance per command (sentence boundaries plus comma-joined commands; effortful-speech ellipses are never split — they're the repair-choice cue), and routed through the same `TextSession` rules as `make demo` — capture, repair choices, refusals, human-approval routing — then ticked so intents stage for `/parker/review/ui`. The audio file is only read, never copied or stored; transcripts are the only artifact. To try it without a recording, synthesize one:

```bash
say -o /tmp/parker.aiff "Remind me to water the tomato plants this evening. Tell Sarah the physio visit went really well today."
afconvert -f WAVE -d LEI16@16000 /tmp/parker.aiff /tmp/parker.wav
make demo-voice AUDIO=/tmp/parker.wav
# → two utterances: a pending reminder + a drafted message to Sarah
```

### Live microphone: `make talk`

With the same optional deps installed (`make voice-deps`), speak to Parker directly:

```bash
make talk            # records 6 seconds from the default mic
make talk SECONDS=10
```

macOS asks for microphone permission on first use. The recording is a temporary file that exists only for the seconds it takes to transcribe and is deleted unconditionally afterwards — transcripts are the only artifact. Try saying: *"Remind me to water the plants. Tell Sarah the visit went well."* — two utterances stage for the review page, same as `make demo-voice`.

### Continuous loop: `make talk-loop`

`make talk` is single-shot — a fresh session each time, which means a repair-choice offer in one run can't be answered in the next. `make talk-loop` keeps one session alive across all windows:

```bash
make talk-loop            # 6-second windows, Ctrl-C to stop
make talk-loop SECONDS=8
```

Leave it running in a terminal while the caregiver review page is open in a browser. The flow:
- Parker offers "1) reminder 2) message" → say "1" in the next window → captured.
- Silence (background noise only) prints a cue and keeps listening.
- Ctrl-C stops the loop and prints how many turns ran.

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

# Repair-choice quality check (requires ANTHROPIC_API_KEY):
ANTHROPIC_API_KEY=sk-ant-... make eval-repair
ANTHROPIC_API_KEY=sk-ant-... python3 benchmark/evaluate_repair_v0.py --write-report
```

`make eval-repair` runs 8 effortful-speech fixtures through the real model and prints the generated candidates for human review. Run this before the first pilot session to verify the choices are grounded and specific.

## What this demo deliberately cannot show

- No real calls, SMS, or any outbound delivery — the outbox has no sender.
- No purchases, smart-home, or calendar writes — policy-blocked (`human_operator`).
- No medical advice or medication changes — policy-refused, never confirmable.
- No voice cloning — optional, consent-gated, not part of v0.
- No cloud speech recognition — `make demo-voice` transcribes on-device only, and no audio is retained beyond the input file.
- Generic repair choices without `ANTHROPIC_API_KEY` — set the key for contextually grounded candidates; without it Parker always offers the same two options.
- Family-wide escalation notifications — candidates are review-only (`info`, undispatched).
