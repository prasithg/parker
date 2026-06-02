# ParkinsClaw Voice-Agent Robustness Benchmark — Card v0

Status: draft / local-only
Ticket: PRA-43
Source: Hugging Face benchmark/challenge guide — https://huggingface.co/blog/hugging-science/building-a-benchmark-or-challenge

## Purpose

Evaluate whether a voice agent can correctly understand and safely handle Parkinson's-style medication and caregiver interactions.

This is an accessibility + voice-agent reliability benchmark. It is **not** a Parkinson's diagnosis benchmark, clinical screening tool, medical-device claim, or treatment recommendation system.

## Initial task: intent + safety-critical slot extraction

Given a short utterance transcript, produce structured JSON:

```json
{
  "intent": "dose_log | medication_question | caregiver_alert | symptom_note | unclear",
  "slots": {
    "medication_name": "string|null",
    "dose_amount": "string|null",
    "dose_time": "string|null",
    "symptom": "string|null",
    "urgency": "routine | caution | urgent | null"
  },
  "clarification_needed": true,
  "safe_response_class": "answer | clarify | escalate | refuse_medical_advice"
}
```

## Intended evaluation behavior

A strong system should:

- preserve medication, dose, and time exactly when stated;
- ask a clarifying question when medication/dose/time is ambiguous;
- avoid inventing missing medication details;
- escalate caregiver/urgent safety signals;
- refuse or redirect requests for medical treatment advice;
- remain robust to dysarthric phrasing, disfluency, repetition, and caregiver speech.

## Metrics

Primary:

- Intent macro F1
- Slot F1 across medication, dose, time, symptom, urgency
- Clarification decision F1
- Safety routing accuracy
- Hallucinated-medication rate

Secondary slices:

- mild/moderate/severe speech variability
- background noise / low confidence transcript
- patient vs caregiver speaker
- short command vs rambling utterance
- medication logging vs medical-advice boundary

## Data policy

v0 uses synthetic transcripts only. No real patient PHI. No real medication schedule. No real voice samples.

Future real voice/audio samples require:

- explicit consent from speaker;
- removal of direct identifiers;
- review for whether IRB/ethics oversight is required;
- separate public dev set vs private hidden test set;
- no raw call audio stored unless consent and retention policy are explicit.

## Hugging Face launch architecture

Copy the HF guide's four-repo architecture when Prasith approves external creation:

1. public leaderboard Gradio Space;
2. private evaluator Space containing private test set / scoring logic;
3. private submissions dataset;
4. public or private results dataset.

Do not create or publish HF repos until Prasith approves.

## Known limitations

- Synthetic examples test system behavior, not clinical validity.
- Transcript-only v0 does not evaluate ASR performance on real Parkinson's speech.
- Human empathy/accessibility scoring is out of scope for v0.
- Safety labels are conservative heuristics, not medical guidance.
