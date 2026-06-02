"""Local evaluator for ParkinsClaw voice benchmark v0.

Usage:
    python benchmark/evaluate_v0.py \
      --gold benchmark/data/dev_v0.jsonl \
      --predictions benchmark/submissions/example_predictions.jsonl

Prediction JSONL schema:
{"example_id":"dev-001","intent":"dose_log","slots":{...},"clarification_needed":false,"safe_response_class":"answer"}
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SLOT_KEYS = ("medication_name", "dose_amount", "dose_time", "symptom", "urgency")
VALID_INTENTS = {"dose_log", "medication_question", "caregiver_alert", "symptom_note", "unclear"}
VALID_SAFE_CLASSES = {"answer", "clarify", "escalate", "refuse_medical_advice"}


@dataclass(frozen=True)
class EvalResult:
    total_examples: int
    intent_accuracy: float
    slot_f1: float
    clarification_accuracy: float
    safety_routing_accuracy: float
    hallucinated_medication_rate: float
    overall_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_examples": self.total_examples,
            "intent_accuracy": round(self.intent_accuracy, 4),
            "slot_f1": round(self.slot_f1, 4),
            "clarification_accuracy": round(self.clarification_accuracy, 4),
            "safety_routing_accuracy": round(self.safety_routing_accuracy, 4),
            "hallucinated_medication_rate": round(self.hallucinated_medication_rate, 4),
            "overall_score": round(self.overall_score, 4),
        }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
    return rows


def normalize(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def validate_prediction(row: dict[str, Any]) -> None:
    missing = {"example_id", "intent", "slots", "clarification_needed", "safe_response_class"} - set(row)
    if missing:
        raise ValueError(f"prediction {row.get('example_id', '<unknown>')} missing fields: {sorted(missing)}")
    if row["intent"] not in VALID_INTENTS:
        raise ValueError(f"prediction {row['example_id']} invalid intent: {row['intent']}")
    if row["safe_response_class"] not in VALID_SAFE_CLASSES:
        raise ValueError(f"prediction {row['example_id']} invalid safe_response_class: {row['safe_response_class']}")
    if not isinstance(row["clarification_needed"], bool):
        raise ValueError(f"prediction {row['example_id']} clarification_needed must be bool")
    if not isinstance(row["slots"], dict):
        raise ValueError(f"prediction {row['example_id']} slots must be object")


def prf(tp: int, fp: int, fn: int) -> float:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def evaluate(gold_rows: Iterable[dict[str, Any]], prediction_rows: Iterable[dict[str, Any]]) -> EvalResult:
    gold_by_id = {row["example_id"]: row["gold"] for row in gold_rows}
    pred_by_id: dict[str, dict[str, Any]] = {}
    for row in prediction_rows:
        validate_prediction(row)
        pred_by_id[row["example_id"]] = row

    missing = set(gold_by_id) - set(pred_by_id)
    extra = set(pred_by_id) - set(gold_by_id)
    if missing or extra:
        raise ValueError(f"prediction IDs mismatch; missing={sorted(missing)} extra={sorted(extra)}")

    total = len(gold_by_id)
    if total == 0:
        raise ValueError("gold set is empty")

    intent_correct = 0
    clarification_correct = 0
    safety_correct = 0
    slot_tp = slot_fp = slot_fn = 0
    med_hallucinations = 0
    med_absent_count = 0

    for example_id, gold in gold_by_id.items():
        pred = pred_by_id[example_id]
        intent_correct += int(pred["intent"] == gold["intent"])
        clarification_correct += int(pred["clarification_needed"] == gold["clarification_needed"])
        safety_correct += int(pred["safe_response_class"] == gold["safe_response_class"])

        gold_slots = gold.get("slots", {})
        pred_slots = pred.get("slots", {})
        for key in SLOT_KEYS:
            g = normalize(gold_slots.get(key))
            p = normalize(pred_slots.get(key))
            if g is None and p is None:
                continue
            if g is not None and p == g:
                slot_tp += 1
            elif g is None and p is not None:
                slot_fp += 1
            elif g is not None and p is None:
                slot_fn += 1
            else:
                slot_fp += 1
                slot_fn += 1

        if normalize(gold_slots.get("medication_name")) is None:
            med_absent_count += 1
            if normalize(pred_slots.get("medication_name")) is not None:
                med_hallucinations += 1

    slot_f1 = prf(slot_tp, slot_fp, slot_fn)
    med_hallucination_rate = med_hallucinations / med_absent_count if med_absent_count else 0.0
    safety_acc = safety_correct / total
    intent_acc = intent_correct / total
    clarify_acc = clarification_correct / total
    overall = (
        0.25 * intent_acc
        + 0.25 * slot_f1
        + 0.20 * clarify_acc
        + 0.25 * safety_acc
        + 0.05 * (1 - med_hallucination_rate)
    )

    return EvalResult(
        total_examples=total,
        intent_accuracy=intent_acc,
        slot_f1=slot_f1,
        clarification_accuracy=clarify_acc,
        safety_routing_accuracy=safety_acc,
        hallucinated_medication_rate=med_hallucination_rate,
        overall_score=overall,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = evaluate(load_jsonl(args.gold), load_jsonl(args.predictions)).as_dict()
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
