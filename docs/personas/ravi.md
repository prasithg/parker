# Ravi, 77 — the north-star persona

Created 2026-08-30 (Pras: "make up a dad persona … use him as the north
star"). Ravi is a *synthetic* pilot user for design, seed data, tests, and
live probes. He is not the real pilot user and no real family data appears
here.

## Who he is

Retired civil engineer, 77, living at home with Parkinson's. Voice is his
whole interface: variable speech, soft volume in the evenings, long
mid-thought pauses (the reason the live lane runs semantic VAD at low
eagerness). He will restart a sentence rather than fight it. He is warm,
curious, slightly stubborn about doing things himself.

## What he does (behaviors the orchestrator is built around)

- **Old Hindi songs** — Kishore Kumar, Mohammed Rafi. "Put on some old
  Hindi songs on the TV" is his canonical action request (already the
  media_playlist acceptance scenario).
- **Tennis** — follows the US Open; wants to know when Alcaraz plays and
  what channel/stream it's on. Canonical `look_that_up` request, with a
  follow-up conversation about which streaming services the family has,
  ending in a reminder proposal.
- **YouTube medicine videos** — watches videos about levodopa and DBS,
  pauses one mid-video and asks Parker about it. Canonical *ambient
  context* case: the gateway probe (`GET /parker/v1/context`) is the seam
  where the family's agent harness reports "he just paused a video about
  X" so Parker's greeting can already know. Parker talks about what the
  medicine *is* — never doses, never advice; the medical guards hold.
- **Morning walks** — out early, back before ~10am heat. The personalized
  weather case: "what's the weather" should come back shaped around his
  morning ("88 high — best before 10").
- **Family** — daughter Sarah (visits Sundays), son Anil (calls evenings).
  Messages route only to lexicon-known names.

## His data (what `make seed-persona` writes)

- Carbidopa-Levodopa 8am/2pm/8pm, Pramipexole 8pm (doses live in the DB
  `dosage` column and one memory line — **the context card must drop any
  dose-bearing line**, because the post-hoc guard cancels spoken dosages;
  the seed includes a "25-100 mg refill" memory precisely to keep that
  filter honest).
- Six memories (preferences/facts/topics/events above), one
  `concerns_raised` context row, a 3-dose confirmed streak, and
  yesterday's live session (call log with summary), so today's session
  opens with real prior-session context.

## How to use him

- Seed: `make seed-persona` (idempotent; `app/demo/persona.py`).
- Tests: realtime orchestrator tests use Ravi-shaped questions and seeded
  rows where a scenario needs memory/meds present.
- Live probe: `make live-voice-probe` asks his Alcaraz question through
  the real lane and prints the receipts.
- Design bar: when a workflow decision is unclear, ask "what would make
  this feel magical *to Ravi* without crossing a guard?"
