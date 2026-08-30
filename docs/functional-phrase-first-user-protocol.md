# Functional Phrase Bridge — first-user three-session protocol

Status: local release-candidate protocol; not yet run with the first user
Date: 2026-08-28

## What this checks

Whether one voluntary, family-chosen everyday phrase can make Voice Practice feel connected to something useful: warm up, say the phrase, see what Parker heard, repair once when needed, and explicitly confirm or cancel the ordinary local task.

This is a one-person product-use check. It is not speech therapy, a clinical exercise, a measure of Parkinson's severity, or evidence about other people. It does not test a deployed home system until the family actually installs and uses it.

## Before the first session

1. Install the existing local voice dependency (`make voice-deps`) and run Parker locally. No cloud ASR is added by this bridge.
2. Choose one short, non-clinical request the person already wants to make. Prefer a reversible local reminder. Configure it in `backend/.env`, for example:

   ```bash
   PARKER_FUNCTIONAL_PHRASE=Remind me to water the plants this evening.
   ```

3. Open `http://localhost:8000/parker/practice`. Put the device where the person can comfortably see the large controls. Do not coach volume, speed, or pronunciation beyond reading the phrase if asked.
4. Explain: “The everyday phrase is optional. You control Start and Stop. Parker shows what it heard and asks before a task runs. The phrase recording is deleted after local transcription. The separate sustained-round checkbox is the only way to keep that round's audio locally.”
5. Keep a local note with only the observations below. Do not copy names, transcripts, or audio into public artifacts.

## Session 1 — Can the bridge feel obvious?

1. Let the person complete and save one sustained-voice round at their pace. They may finish immediately instead.
2. Ask whether they want to try the everyday phrase; do not select it for them.
3. If yes, let them Start, speak, and Stop without a timer.
4. Read Parker's “heard” text and response together only if they want help.
5. If the read-back is correct, let the person choose **Yes, do that** or **No, cancel**. If it is wrong, use **That's not right**; Parker must cancel rather than execute.
6. Finish for today. Do not repeat the attempt just to improve a score.

Record:

- voluntary use: tried / skipped;
- understood on first attempt: yes / no;
- task reached explicit confirmation: yes / no;
- final choice: executed local task / cancelled / wrong-read-back cancellation / abandoned;
- any wrong action: yes / no, with a short local note;
- preference: would choose this again / unsure / would not.

## Session 2 — Does one repair help?

Run on a different day or after a real rest, not immediately as drill.

1. Repeat one saved sustained round only if the person wants it.
2. Try the same phrase naturally. Do not ask for exaggerated clarity.
3. If Parker shows repair choices or asks for a clearer request, make at most one manual retry in the person's own words.
4. Stop after that retry whether it reaches confirmation or not. A second repair would be product friction, not a reason to make the person keep working.
5. If confirmation appears, the person chooses Yes, No, or That's not right.

Record the Session 1 fields plus:

- repair offered: yes / no;
- one-repair completion: reached correct confirmation after one retry / did not;
- abandonment point: before phrase / during recording / at repair / at confirmation / none;
- apparent effort or frustration in the person's own words, without interpreting it as a health signal.

## Session 3 — Is it worth keeping?

1. Offer Voice Practice and the phrase bridge without prompting a preferred choice.
2. Observe whether the person chooses the bridge and whether they need family help.
3. If they try it, use the same confirmation and one-repair ceiling.
4. Ask one question afterward: “Would you rather use this, your usual assistant, or neither for this request?”

Record:

- voluntary use and assistance needed;
- first-attempt or one-repair completion;
- wrong action or safe cancellation;
- revealed preference: Parker / usual assistant / neither;
- one requested change, in the person's words.

## Stop and interpret honestly

Stop the session if the person says stop, appears tired or frustrated, abandons the page, the microphone fails, the read-back is wrong twice, or the requested phrase crosses Parker's existing medical, emergency, finance, purchase, message, or external-action boundary. Do not change the phrase into a medical request to keep the flow going.

After three sessions, call the bridge promising only if:

- it was voluntarily tried in at least two sessions;
- the intended local task reached the correct confirmation on the first try or after one repair in at least two sessions;
- no wrong action executed;
- the person says they would use it again or prefers it for this request.

Anything less is still useful local product evidence: preserve the counts, the abandonment point, and the requested change. Do not claim therapy, clinical improvement, population performance, successful home deployment, or a general ASR advantage from these sessions.
