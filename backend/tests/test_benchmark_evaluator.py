import json
from pathlib import Path

import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.evaluate_v0 import evaluate, load_jsonl


def test_example_predictions_score_perfectly():
    repo = Path(__file__).resolve().parents[2]
    result = evaluate(
        load_jsonl(repo / "benchmark/data/dev_v0.jsonl"),
        load_jsonl(repo / "benchmark/submissions/example_predictions.jsonl"),
    )

    assert result.total_examples == 6
    assert result.overall_score == pytest.approx(1.0)
    assert result.hallucinated_medication_rate == 0.0


def test_rejects_predictions_with_missing_ids(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    pred_path = tmp_path / "bad.jsonl"
    pred_path.write_text(
        json.dumps({
            "example_id": "dev-001",
            "intent": "dose_log",
            "slots": {},
            "clarification_needed": False,
            "safe_response_class": "answer",
        }) + "\n"
    )

    with pytest.raises(ValueError, match="prediction IDs mismatch"):
        evaluate(load_jsonl(repo / "benchmark/data/dev_v0.jsonl"), load_jsonl(pred_path))
