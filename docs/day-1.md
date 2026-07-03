# Day 1 with Parker

The working-backwards spec for Parker's first day in a real home, written as
if the product were finished. Anything not yet true is marked **[gap]** and
feeds the slice queue at the bottom.

Two people have a Day 1, and they are different products:

- **The administrator** (a family member — in the pilot, Pras) installs and
  configures Parker. About thirty minutes, once.
- **The user** (the person with Parkinson's — in the pilot, Dad) talks to
  Parker. Voice is his entire interface. He configures nothing, ever.

**v0 scope decision (2026-07-02):** Parker does not send anything out of the
house. No external messages, no calls, no posts. It is an assistant that
listens, talks back, answers well because it has context on the user's own
digital life (browsing, YouTube, mail, calendar), and acts *on the user's own
computer* — opening things, finding things, putting things on the calendar.
Family stays informed through the digest they check, not through messages
Parker pushes. Outbound send paths return in a later version, deliberately.

---

## Part 1 — The administrator's Day 1 (~30 minutes)

You need: the family Mac (a Mac mini in the living room works), a USB
microphone near where the user sits, and the user's own macOS account.

1. **Install.** Download `Parker.dmg`, drag Parker.app into Applications
   under the user's account. No Python, no terminal, no dependencies.
   **[gap: Session F+G, in flight — Part 1 is its acceptance checklist]**
2. **Mic check.** Onboarding asks for microphone permission, downloads the
   local speech model (one-time; speech recognition is local forever after),
   then has you say a sentence and repeats back what it heard. Do this
   *from the user's chair*, at conversation volume, with the TV murmuring —
   not leaning into the mic. **[gap: F+G]**
3. **Connect intelligence.** Paste one Anthropic API key. This powers the
   brain — open questions and computer actions. Everything routine
   (reminders, repair, confirmation) runs locally and works even with no
   key at all; Parker must say so and degrade gracefully, not break.
   Fast conversational replies route to a fast model; heavier agent work
   routes to a bigger one — same key, per-task routing. **[gap: key entry
   UI is F+G; per-task model routing not yet built]**
4. **Pick the voice.** Parker speaks. v0 ships with the best local macOS
   voices (download a premium one in the picker — free, offline). A
   natural cloud voice is a later admin toggle; the aspiration is a warm,
   familiar timbre the user *wants* to answer — licensed voices only,
   never a cloned real person without consent. *(exists:
   `PARKER_TTS_VOICE`; picker is **[gap: F+G]**)*
5. **Connect his world.** This is what separates Parker from a smart
   speaker. Sign in / point Parker at, read-only:
   - **Calendar** (read + the one write scope: adding events)
   - **Mail** (read-only)
   - **Browsing history & YouTube** (his Chrome profile, local read)
   All of it stays on the Mac: context is indexed locally, only the
   minimum needed for an answer ever goes to the model, raw data is never
   uploaded wholesale or stored by Parker beyond its local index.
   **[gap: context layer — biggest unbuilt piece of v0]**
6. **Teach it the names.** The personal lexicon: family names, doctors,
   places ("Sarah", "Priya", "Leander"). This is what makes names survive
   imperfect speech recognition on the first try. *(exists:
   `PERSONAL_LEXICON`; settings surface **[gap: F+G]**)*
7. **Set guardrails once.** Approve capabilities, not actions: answers,
   reminders, calendar writes, exercises, and computer actions Parker may
   take on his Mac. Within an approved capability, the user's own spoken
   "yes" is the only gate. You are not an approval queue for your parent.
   *(exists: capability autonomy + voice-confirmation seam)*
8. **One real test, then hands off.** From the chair: "Remind me to take
   my pills at six." Parker repeats it back, asks "Shall I go ahead — yes
   or no?", he says yes, it lands on the calendar and the screen.

**The rule: don't leave the house until the user has been understood once
without your help** — a sentence he said, understood and acted on, while
you stood behind the couch and said nothing.

Afterwards, check the family digest (`/parker/digest`) whenever you like —
a rearview mirror of what Parker did, not a queue of things to approve.

---

## Part 2 — The user's Day 1

No setup. Parker listens near the chair and talks back in a voice, not a
beep. Pauses, restarts, soft trailing endings, a word that comes out twice —
all fine; that is exactly the speech Parker is built for. **[gap: hands-free
entry — today an admin starts the loop; "Hey Parker" / safe ambient
listening is not built]**

The first morning, as it should actually go:

**"What's on my calendar today?"** — Parker reads out the day. First proof
it knows *his* life, not trivia. *(needs: context layer)*

**"Remind me to take my medication at six o'clock."** — Repeated back,
confirmed with a spoken yes, on the calendar and the screen. Nothing ever
happens without that yes. *(exists end-to-end today)*

**"What was that video I was watching yesterday about airplane engines?"**
— Parker finds it in his YouTube history and offers to put it on the
screen. Google Home has no answer to this; this is the moment Parker wins
the countertop. *(needs: context layer + screen/CUA)*

**"Find homes for sale in Leander and open them on my computer."** — The
brain does the search, Parker opens results on his Mac, narrates what it
did. Acting on *his* computer, in front of him — no message leaves the
house. *(needs: CUA/hands slice)*

**"Let's do my speech exercises."** — A short session, paced to his
speech, never the other way around. *(exists)*

**And the most important one — being misheard.** When Parker isn't sure,
it never guesses and never acts on a guess. One short question: "Did you
mean remind you to call Michael — yes or no?" One repair, then done. If
still unsure, it says so and drops it. No phantom reminders, no actions he
didn't ask for. *(exists — this is the harness's protected metric)*

What he should *never* experience on Day 1: repeating himself more than
once per request; Parker acting without his yes; anyone else approving his
requests; any hint his voice or his mail leaves the house (it doesn't).

**Success criterion: within the first ten minutes in the chair — one
question about his own life answered correctly, one reminder set by voice
alone, zero help from anyone in the room.**

The measurable bar behind it is the North Star: understood first-try or
after one repair ≥90% (harness today: 82% on 333 real+synthetic clips,
0 unsafe captures; stock assistants sit near 50% for this user).

---

## The slice queue (what Day 1 exposes, ranked)

1. **Install/onboarding** — Session F+G (Tauri + sidecar), in flight.
   Now must also include: API-key entry with graceful no-key degrade,
   voice picker, context sign-ins, lexicon/guardrail settings surfaces.
2. **Context layer** — local index over calendar, mail, browsing/YouTube
   history; retrieval into brain answers with a privacy contract (local
   index, minimal excerpts to the model, nothing stored server-side).
   The biggest unbuilt piece of the v0 story.
3. **Hands-free invocation + latency** — wake entry so nobody launches
   anything, plus streaming so replies feel conversational (brain tail
   currently 11.76 s max). If Haiku-class routing isn't fast enough,
   evaluate Groq/Cerebras-hosted models for the conversational tier.
4. **CUA/hands v0** — Parker acts on his Mac: open a video, show search
   results, add a calendar event. Capability-gated, confirmed by voice,
   local-only effects. (The OpenClaw-gateway design from Session C is the
   likely vehicle.)
5. **Mic reality** — PD400X vs phone A/B through the harness, from the
   chair at room distance.
6. **Med-adherence v0** — ask-first check-ins surfacing in the digest
   (no outbound alerts in v0; alerting returns with send paths later).
7. **Voice upgrade** — licensed natural TTS as an admin toggle.

Deferred by the v0 scope decision: all outbound send paths (iMessage,
email, WhatsApp, Discord, phone calls) and pushed family alerts. The
capture→confirm→outbox machinery stays; released messages simply stop at
the digest until send paths return.

Update this document whenever a gap closes; it is the demo script for the
first real install and the outline of the eventual public post.
