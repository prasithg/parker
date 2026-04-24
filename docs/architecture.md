# ParkinsClaw Architecture

## Overview

ParkinsClaw is a voice-first companion that makes scheduled phone calls to a Parkinson's patient using a cloned family voice. The patient talks; the system handles everything else.

## Data flow

```
Scheduler → Twilio API → outbound call to patient
                          ↓
Patient answers → Twilio Media Stream (WebSocket) → FastAPI backend
                                                     ↓
                                              OpenAI Realtime API
                                              (speech-to-speech + tools)
                                                     ↓
                                          ElevenLabs voice clone output
                                                     ↓
Patient hears cloned voice ↔ conversation loop
                                                     ↓
Tool calls: med reminder, mood check, cognitive exercise
                                                     ↓
Call ends → summary + metrics → SQLite
                                     ↓
                              Family dashboard reads DB
```

## Components

### 1. Call Scheduler (`backend/app/calls/scheduler.py`)
- Runs as a background task in FastAPI (APScheduler)
- Triggers outbound calls at configured times (morning check-in, med reminders)
- Reads medication schedule from DB

### 2. Call Handler (`backend/app/calls/handler.py`)
- Receives Twilio webhook on call answer
- Opens WebSocket for Twilio Media Stream
- Bridges audio between Twilio and OpenAI Realtime API
- Uses ElevenLabs for TTS output in cloned voice

### 3. Conversational Agent (`backend/app/conversation/agent.py`)
- OpenAI Realtime API with tool calling
- System prompts vary by call type (check-in, med reminder, evening chat)
- Tools: `log_medication`, `record_mood`, `cognitive_exercise`, `escalate_to_family`

### 4. Voice Clone Manager (`backend/app/voice/clone.py`)
- Manages ElevenLabs voice clones
- One clone per family member (consent required)
- Stores voice IDs in config

### 5. Medication Tracker (`backend/app/meds/tracker.py`)
- Medication schedule in SQLite
- Logs: med name, scheduled time, confirmed time, patient response
- Flagging for missed doses → escalation

### 6. Database (`backend/app/db/`)
- SQLite with SQLAlchemy
- Tables: patients, voices, medications, call_logs, dose_logs, mood_entries
- Migration via Alembic

### 7. Family Dashboard (`dashboard/`)
- Simple React app
- Shows: call history, medication adherence, mood trends, alerts
- Reads from backend API

## Security boundaries

- API keys in environment variables, never committed
- Patient phone number in .env, not in code
- Voice clone consent records stored locally
- Raw call audio NOT stored — only transcripts + summaries
- Dashboard behind basic auth (v0)

## Cost estimate (MVP)

| Service | Usage | Monthly cost |
|---------|-------|-------------|
| Twilio | ~60 min/mo (2 calls/day × 10 min) | ~$6 |
| ElevenLabs | Creator plan + usage | ~$22 |
| OpenAI Realtime | ~60 min/mo | ~$6 |
| Fly.io | Hobby instance | ~$5 |
| **Total** | | **~$39/mo** |
