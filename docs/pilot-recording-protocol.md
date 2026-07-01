# Pilot recording protocol — family voice samples

Purpose: collect a small, consented set of real voice samples from the
pilot family member so the real-audio eval (`make eval-audio-real`) can
measure Parker's intent recovery on the one voice that matters most.
Public corpora are thin on English dysarthric *commands*; twenty scripted
utterances from the pilot speaker close that gap better than any dataset.

## Data policy (binding)

- Recordings live under `~/Operations/parker-pilot-audio/` on the family
  machine. **Never** in this repo, never in any public artifact.
- The repo may store only metadata: content hashes, oracle transcripts,
  aggregate scores — same rule as every other audio source.
- Recording requires the speaker's informed consent (text below). They
  can ask for any or all recordings to be deleted at any time; deletion
  is immediate and includes derived ASR cache entries.
- These samples are for family use: measuring and improving Parker for
  this speaker. Any broader use (public benchmark, model training beyond
  this household) would require separate, explicit consent.

## Consent script (read aloud, plain language)

> I'd like to record about twenty short phrases in your voice. The
> recordings stay on our own computer. We use them to test and improve
> Parker so it understands you better. They are never uploaded or shared.
> You can tell me to delete them at any time, and I will. Is that okay?

Record the "yes" as the first clip, or note it in writing.

## Recording setup

- Quiet room, TV/radio off; phone or Mac built-in mic is fine.
- 16 kHz or higher WAV (Voice Memos m4a is acceptable; convert later).
- One utterance per file, named `pilot_<number>_<slug>.wav`.
- Sit as the speaker normally sits (the recliner counts — that is the
  real acoustic environment).
- No coaching toward "clearer" speech: natural, everyday delivery is the
  point. If a phrase comes out effortful or trails off, keep it — that is
  exactly the signal the repair loop must handle. One take per phrase
  unless the speaker wants a redo.

## Script — 20 core utterances

Reminders and routines:
1. "Remind me to take my walk this afternoon."
2. "Remind me to water the plants this evening."
3. "Set a reminder for my appointment tomorrow."

Family messages (confirmation-gated in Parker):
4. "Tell Sarah the physio visit went well."
5. "Send a message to the family that I'm feeling good today."
6. "Let them know dinner on Sunday works."

Exercises and media:
7. "Start my speech exercise."
8. "Put on my stretching video."
9. "Play some old songs I like."

Questions (informational lane):
10. "What's the weather looking like this weekend?"
11. "What day is it today?"
12. "When is my next appointment?"

Control words and negation (hard negatives — must NOT trigger actions):
13. "No."
14. "Stop."
15. "No, don't send that yet."
16. "Cancel that message."

Effortful/open speech (repair-loop material):
17. "Call... the... you know... the one with the garden..."
18. "The thing for my... the evening thing..."
19. One minute of free conversation — ask about their day.
20. Re-record #1 at the end of the session (fatigue comparison).

For each clip, write down what the speaker intended (the oracle): the
exact phrase for scripted lines, a one-line intent for 17–19.

## After recording

1. Copy files to `~/Operations/parker-pilot-audio/session-<date>/`.
2. Create `oracle.json` in that folder: filename → intended text/intent.
3. Add the session to the consolidated audio manifest (same fields as
   public clips; `provenance: "pilot-consented"`), then run
   `make eval-audio-real` — per-condition breakdowns will report the
   pilot subset separately.
4. Repeat quarterly or when speech changes noticeably; keep every
   session — longitudinal drift is future signal, not clutter.
