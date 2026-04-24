# ParkinsClaw

Voice-first companion for Parkinson's patients. Calls in a family member's voice. No app required.

**Built for one person first.**

## How it works

1. **Scheduled outbound call** — Twilio calls the patient at set times
2. **Cloned voice greeting** — ElevenLabs voice clone of a family member speaks naturally
3. **AI conversation** — OpenAI Realtime API handles the dialogue, with tool calling for reminders
4. **Medication reminders** — agent asks about specific meds, records proof of dose
5. **Call log** — summary, duration, mood, medication status → SQLite
6. **Family dashboard** — simple web view of call history and adherence

## Stack

| Layer | Tech |
|-------|------|
| Telephony | Twilio Programmable Voice + Media Streams |
| Voice clone | ElevenLabs |
| Conversational AI | OpenAI Realtime API |
| Backend | Python / FastAPI |
| Database | SQLite (v0) |
| Dashboard | React / Next.js |
| Hosting | Fly.io or Railway |

## Project structure

```
parkinsclaw/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry point
│   │   ├── config.py            # Settings from env vars
│   │   ├── calls/
│   │   │   ├── router.py        # Call scheduling + initiation
│   │   │   ├── handler.py       # Twilio webhook + media stream
│   │   │   └── scheduler.py     # Cron-style call scheduling
│   │   ├── voice/
│   │   │   ├── clone.py         # ElevenLabs voice clone management
│   │   │   └── stream.py        # Audio streaming during calls
│   │   ├── conversation/
│   │   │   ├── agent.py         # OpenAI Realtime agent logic
│   │   │   ├── prompts.py       # System prompts for different call types
│   │   │   └── tools.py         # Tool definitions (med reminders, mood, etc.)
│   │   ├── meds/
│   │   │   ├── models.py        # Medication schedule data models
│   │   │   └── tracker.py       # Dose verification + logging
│   │   ├── dashboard/
│   │   │   └── router.py        # API endpoints for family dashboard
│   │   └── db/
│   │       ├── database.py      # SQLite connection + migrations
│   │       └── models.py        # SQLAlchemy models
│   ├── requirements.txt
│   ├── .env.example
│   └── tests/
│       └── ...
├── dashboard/
│   ├── package.json
│   └── src/                     # React/Next.js family dashboard
├── docs/
│   └── architecture.md
├── docker-compose.yml
├── Makefile
└── README.md
```

## Setup

```bash
# Backend
cd backend
cp .env.example .env  # fill in API keys
pip install -r requirements.txt
uvicorn app.main:app --reload

# Dashboard
cd dashboard
npm install
npm run dev
```

## Constraints

- One patient. No multi-tenant.
- Phone calls only — patient never installs anything.
- Voice cloning requires explicit consent from the person being cloned.
- Companion + reminders only. No medical advice, diagnosis, or treatment recommendations.
- Data structured for future HIPAA compliance but not compliant in v0.
- < $50/month infrastructure.

## License

Personal project. Not licensed for distribution.
