# Parker problem, value proposition, and first-user introduction

Date: 2026-08-29
Status: strategy hypothesis for the first household; not a public claim

## Decision

Parker's first problem is not "Parkinson's needs an AI assistant." It is not reminders, Voice Practice, a caregiver dashboard, or a general 360-degree service either.

The first problem is:

> Dad's voice is the easiest way for him to use technology, but today's voice assistants make him fight to be heard. They miss the wake phrase, time out while he is forming a thought, go silent, do the wrong thing, ignore stop, or lose the thread of the conversation.

Dad's job is:

> Let me ask what I want, in my own time, without typing, repeating the whole thought, or fighting the machine. If the machine is unsure, help me repair the request without making me start over. Keep me in control.

This is consistent with Dad's actual use of Google Home: weather, sports scores, questions prompted by conversation, and things he becomes curious about after watching a video. Typing is the hardest input. Touch is available. Voice is preferred, but his requests may be short, include long pauses, trail off, or restart.

Parkinson's UK describes the same communication burden more broadly: people may need more time to get thoughts together, find it hard to respond in the flow of conversation, struggle to change topics, and expend more effort to speak.[15] Parkinson-specific voice-technology studies report frustration with repetition, devices timing out before a thought is finished, and the lack of real conversation.[16][17]

## The Uri Levine lens

The useful lesson from *Fall in Love with the Problem, Not the Solution* is an operating discipline, not a Waze analogy.

Levine's authorized excerpt says to identify a consequential problem, ask who has it, speak with those people about their perception of it, and only then build. It also recommends telling the story as "we help these people avoid this problem," not "our system does these things."[8] In a later interview, Levine calls the problem the mission and the North Star; solutions should remain experiments.[14]

Applied to Parker:

- Dad defines whether the problem is real and whether the outcome is useful. Pras is still a sample of one builder.
- Wake mode, VAD, Whisper, repair choices, the Dad Screen, reminders, Voice Practice, OpenClaw, and realtime speech are replaceable solution hypotheses.
- The decision question is: "Did this let Dad follow his curiosity or complete an intended task with less effort while staying in control?"
- A polite "that's cool" is weak evidence. Voluntary return use, without Pras prompting him, is stronger evidence.
- "Fail fast" must be adapted. A family assistive product can lose trust and dignity. Experiments stay brief, consented, reversible, and low-stakes.

The current code should not dictate the value proposition. If reminders are easy to build but Dad mostly wants information and conversational follow-up, reminders are not the headline.

## Value proposition hierarchy

### Dad-facing one-liner

> Parker gives you time to ask things in your own way. It shows what it heard, asks about the part it is unsure about, keeps the conversation going, and stops when you tell it to.

This is the product promise we should earn. It is not yet a validated outcome.

### Short product statement

> Parker is a more patient personal voice assistant for people who are not always understood by today's voice technology. It gives them time to form a request, helps repair misunderstandings without making them start over, and keeps them in control of what happens next.

### Family-facing statement

> Parker lets your family member speak for themselves instead of reaching for a keyboard or asking someone else to translate for the technology. The family helps configure the tool, but the person remains the requester and decision-maker.

### Current tested prototype statement

The current implementation supports a narrower, truthful claim:

> Say a small local task in your own way. Parker shows its draft, asks one bounded question if it is unsure, and waits for your confirmation before saving or running anything.

The current prototype has strong wake, visible-state, repair, confirmation, cancellation, local reminder, and local-draft machinery. The richer information lane is optional and synchronous; the zero-config path is a stub, the direct Claude brain explicitly lacks live data, and the OpenClaw path is fake-gateway tested but not yet the measured first-user experience. Do not introduce Parker as a better source for weather, sports, or current events until that lane is connected and verified.

## What to say to Dad

### Recommended introduction

> Dad, you know how Google sometimes doesn't hear "Hey Google," cuts you off when you pause, or starts doing the wrong thing and won't stop? I've been making something that tries a different approach. You can take your time. It shows what it thinks you said, and if it isn't sure, it asks one short question instead of guessing. You can correct it or stop it. It's early and I don't know yet if it is actually better. Will you help me try two or three things you would normally ask Google?

### More casual version

> Dad, want to try something I've been working on? It is supposed to be a more patient way to ask things by voice. You don't have to get the sentence out perfectly or all at once. Ask it something you actually care about and tell me where it gets annoying.

### Fifteen-second version

> I made this because typing is a pain and Google doesn't always give you time to finish. Parker tries to wait, shows what it heard, and asks instead of guessing. Want to help me see if it is actually easier?

The phrase "help me test it" fits Dad because he is willing to help, but the session must not turn him into unpaid QA. Use two or three natural interactions, stop within ten minutes, and let him judge the experience.

## What not to say

Do not call it:

- "the Parkinson's AI companion";
- speech therapy at home;
- an AI that understands Parkinson's speech;
- a safer Google Home replacement;
- peace of mind for the family;
- an emergency or monitoring system.

Do not promise that Parker understands him better, learns his voice automatically, improves independence, reduces caregiver burden, improves speech, or is private in every possible path. Those outcomes are not established.

When Parker fails, say "Parker did not get that," not "say it more clearly." Research on adoption and first use shows that usefulness and enjoyment matter, while command construction, misunderstood system behavior, setup friction, and privacy concerns can make an initially simple voice interface feel like work.[4][18][19]

## The right first experience

The best first proof is not a feature tour and probably not a reminder. It is the job Dad already hires Google Home for.

### Go/no-go prerequisite

Do not run the real introduction until Parker can demonstrate this exact loop on the laptop:

1. Dad starts the interaction by touch or wake phrase.
2. Parker visibly enters listening state immediately.
3. Dad asks one real question about weather, sports, a video, or a conversation topic.
4. He may pause, trail off, or restart without being cut off.
5. Parker shows what it heard.
6. If uncertain, it repairs only the unclear part once.
7. Parker gives a brief current answer with a visible source/freshness cue.
8. Dad asks one natural follow-up without restating the topic.
9. "Stop" or the Stop button immediately ends speech and prevents a stale response from landing.
10. Dad answers one neutral question: "Was that easier, the same, or more annoying than Google?"

Studies involving people with Parkinson's specifically call out timeout, repetition, lack of conversation, and the need for extended listening time.[16][17] The first interaction matters: an older-adult study found speech initially appealing, but follow-up reactions suffered when people had to construct special command sentences or misunderstood what the assistant could do.[18]

### First-session rules

- Pras completes setup, downloads, accounts, microphone permission, and source configuration before Dad enters.
- Dad chooses the question. Do not script a success or engineer a misunderstanding.
- Speak to Dad, not the family observer.
- Do not coach volume or pronunciation on the first miss.
- Allow one repair. If it still fails, stop and record the product failure.
- Demonstrate Stop as part of control, not as a failure drill.
- Do not ask for a testimonial. Ask what was easier and what was annoying.
- The real success signal is that Dad later chooses Parker again for another question.

## Product implication: build the curiosity loop next

The next vertical slice should be a Patient Curiosity Loop:

```text
touch/wake -> patient capture -> visible transcript
-> one bounded repair if needed
-> brief current answer with sources
-> context-preserving follow-up
-> immediate stop/cancel
-> one local outcome record
```

This reuses last night's work. Wake gating, truthful visible state, Dad Screen, repair, bounded history, safety guards, confirmation, and cancellation are the brainstem. The missing proof is the fast, current, conversational answer lane.

Do not expand into more task types, a generic proactive system, a content program, or model training before this loop earns voluntary use.

## Hypothesis and falsification

Hypothesis:

> If Parker gives Dad enough time to form a voice request, makes its understanding visible, repairs one unclear detail, provides a useful current answer, preserves one follow-up, and reliably stops, Dad will experience less interaction effort and voluntarily use it again.

Falsifiers:

- touch-to-talk or wake still feels like extra ceremony;
- manual finish is burdensome;
- answers are slower or less trustworthy than Google;
- the screen increases rather than reduces cognitive load;
- one repair is still too much work;
- follow-up context fails;
- Stop is not immediate;
- Dad prefers asking Pras or using Google despite successful operation;
- family setup/support costs more than the value created.

Any of those can change the solution. Dad's problem remains the guide.

## Sources

[4] https://pmc.ncbi.nlm.nih.gov/articles/PMC10956694 — Factors influencing older adults’ acceptance of voice assistants
[8] https://startupnation.com/books/fall-in-love-with-the-problem-not-the-solution — Fall in Love with the Problem, Not the Solution — authorized excerpt
[14] https://www.jeremyutley.com/paint-pipette-heroes-of-innovation/uri-levine — Uri Levine interview — problem as North Star
[15] https://www.parkinsons.org.uk/information/symptoms/non-motor/speech-communication — Parkinson’s UK — Speech and communication
[16] https://pmc.ncbi.nlm.nih.gov/articles/PMC12311396 — Voice technology experiences among people with Parkinson’s and carers
[17] https://pmc.ncbi.nlm.nih.gov/articles/PMC12917486 — Co-designing voice-assisted technology for people with Parkinson’s
[18] https://pmc.ncbi.nlm.nih.gov/articles/PMC7840274 — Older adults’ first interactions with voice assistants
[19] https://pmc.ncbi.nlm.nih.gov/articles/PMC11288472 — How older adults set up voice assistants
