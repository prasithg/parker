# Day 1 with Parker

This is the working-backwards spec for Parker's first day in a real home. It
is written as if the product were finished; anything not yet true is marked
**[gap]** and feeds the slice queue at the bottom. Two people have a Day 1,
and they are different products:

- **The administrator** (a family member — in the pilot, Pras) installs and
  configures Parker. They touch settings once; ideally never again that day.
- **The user** (the person with Parkinson's — in the pilot, Dad) talks to
  Parker. Voice is his entire interface. He configures nothing, ever.

Success on Day 1 is measured from the recliner, not the terminal.

---

## Part 1 — The administrator's Day 1 (about 30 minutes)

You need: a Mac (the family's shared Mac mini works), a USB microphone near
where the user sits, and the user's own macOS account.

1. **Download and install.** Grab `Parker.dmg`, drag Parker.app into
   Applications under the user's macOS account. No Python, no terminal, no
   dependencies. **[gap: Session F+G in flight — this is its acceptance
   criterion]**
2. **Run onboarding.** Parker's menu-bar icon walks you through: microphone
   permission, downloading the local speech model (one-time, local-only
   after that), and a mic check ("say a sentence, Parker repeats what it
   heard"). **[gap: F+G]**
3. **Teach it the family.** Add family contacts (names + how to reach them)
   and the personal lexicon — the names Parker should expect to hear
   ("Sarah", "Priya", the grandkids). This is what makes "tell Sarah I'm
   okay" work on the first try. *(exists: `PARKER_FAMILY_CONTACTS`,
   `PERSONAL_LEXICON`; needs a settings surface instead of env config
   **[gap: F+G onboarding]**)*
4. **Set the guardrails once.** Approve what Parker may do on its own
   (reminders, messages to the family contacts you listed, exercises) —
   capability-level, not per-action. You will not be approving your parent's
   individual requests; within approved capabilities, their own spoken
   confirmation is the only gate. *(exists: capability autonomy +
   voice-confirmation seam)*
5. **Do one end-to-end test.** Sit where the user sits, say "remind me to
   take my pills at six", confirm with "yes", and watch it land on the
   Parker screen. Don't leave until this works from the chair, at
   conversation volume, with the room's normal background noise.
6. **Point them at the rearview mirror.** Bookmark the family digest — the
   daily summary of what Parker did and anything worth knowing. Awareness,
   not an approval queue. *(exists: `/parker/digest`, `make digest`)*

**The rule: don't leave the house until the user has been understood once
without your help.** Not a demo you drive — a sentence they said, understood
and acted on, while you stood behind the couch and said nothing.

---

## Part 2 — The user's Day 1 (no setup, just talking)

Parker listens near the chair. Talk to it like a person; pauses, restarts,
and soft endings are fine — that is exactly what it is built for. **[gap:
hands-free invocation — today the loop must be started by the admin;
wake-word/ambient entry is not built]**

Five things that work on the first day, in the user's own words:

1. **A reminder.** "Remind me to take my medication at six o'clock."
   Parker repeats it back and asks "Shall I go ahead — yes or no?" A "yes"
   sets it. Nothing happens without that yes.
2. **A message to family.** "Tell Sarah I'm doing fine today." Parker
   confirms and sends it to Sarah because Sarah is on the family list —
   no one else needs to approve it.
3. **A question.** "What's the weather like tomorrow?" or "Tell me about
   the trains in India." Parker answers out loud; questions never turn
   into accidental actions. *(this exact failure was found and fixed —
   pronoun-recipient guard)*
4. **Exercises.** "Let's do my speech exercises." Parker leads a short
   session, pacing itself to the user's speech, not the other way around.
5. **Being misheard — the most important one.** When Parker isn't sure, it
   never guesses and never acts on a guess. It asks one short question:
   "Did you mean remind you to call Michael — yes or no?" or offers two
   choices. One repair, then done. If it still isn't sure, it says so and
   drops it — no phantom reminders, no messages to people who don't exist.

What the user should *not* experience on Day 1: being asked to repeat
themselves more than once per request; Parker acting without a spoken yes;
anyone in the family being asked to approve their request; any hint that
their voice is being sent anywhere (it isn't — audio is processed on the
Mac and raw audio is not stored by default).

**The success criterion: within the first ten minutes in the chair, the
user has been understood, one reminder exists, and one message has reached
family — with zero help from anyone in the room.**

The measurable bar behind that sentence is the North Star: understood on
the first try or after one repair question ≥90% of the time (current
harness: 82% on the 333-clip real+synthetic manifest, 0 unsafe captures).

---

## What Day 1 exposes as not-yet-true (the slice queue)

Ranked by how hard each gap breaks the Day 1 story:

1. **Install/onboarding** — Session F+G (Tauri shell + sidecar), in flight.
   Part 1 above is its acceptance checklist.
2. **Hands-free invocation** — wake entry ("Hey Parker" or safe ambient
   listening) so the user can start a request without anyone launching
   anything. Ambient-audio guards from Session D (counting-speech, bare
   digits) are the foundation. Includes the response-latency bar: repairs
   and confirmations must feel conversational (brain tail is currently
   11.76 s max — streaming slice).
3. **Mic reality** — PD400X vs phone A/B through the harness, from the
   chair, at room distance. Decides whether close-capture is enough or the
   rig needs a far-field array.
4. **Med-adherence v0** — ask-first ("did you take your six o'clock
   pills?"), family care-alert when a window passes with no answer. No
   camera, no diagnosis, no medication advice — ever.
5. **Settings surface for contacts/lexicon** — folds into the desktop app
   once F+G lands.

Update this document whenever a gap closes; it is the demo script for the
first real install and the outline of the eventual public post.
