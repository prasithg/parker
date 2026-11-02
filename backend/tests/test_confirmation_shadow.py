"""Boundary tests for the offline confirmation-shadow replay benchmark.

Authored before the implementation from the acceptance list in the
2026-09-22 phase-4 build brief. No network, no provider, no credentials:
the recorded provider rows are a historical fixture, the manifest is a
byte-for-byte copy of the frozen synthetic manifest, and the baseline is
the trusted repository grammar executed from its AST.
"""

import ast
import hashlib
import json
import math
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO))

from benchmark import confirmation_shadow as cs  # noqa: E402

MANIFEST = REPO / "benchmark" / "fixtures" / "confirmation_shadow_synthetic_v1.json"
RECORDED = REPO / "benchmark" / "fixtures" / "confirmation_shadow_jev_recorded_v1.json"
TEXTLOOP = REPO / "backend" / "app" / "conversation" / "textloop.py"
REALTIME = REPO / "backend" / "app" / "parker" / "realtime.py"

# Hashes frozen in the phase-2 protocol before the recorded provider run.
RECORDED_MANIFEST_SHA256 = "074c2492c3e421ca0f7b948dcea5b1d0997f2a17a279f3f8765782b2372da5b0"
RECORDED_TEMPLATE_SHA256 = "ce8480ac5ed299ce52d29d13a882db9708ef0004a595051677a936931d4645bd"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_dict() -> dict:
    return json.loads(MANIFEST.read_text())


def _recorded_dict() -> dict:
    return json.loads(RECORDED.read_text())


def _write(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def _run_with(tmp_path: Path, manifest=None, recorded=None) -> dict:
    manifest_path = _write(tmp_path, "manifest.json", manifest) if manifest is not None else MANIFEST
    recorded_path = _write(tmp_path, "recorded.json", recorded) if recorded is not None else RECORDED
    return cs.run(manifest_path=manifest_path, recorded_path=recorded_path, repo_root=REPO)


def _rows_by_id(result: dict) -> dict:
    return {row["case_id"]: row for row in result["rows"]}


@pytest.mark.parametrize("bad_case", [None, 3, "reply", []])
def test_manifest_rejects_non_object_cases_clearly(bad_case):
    manifest = _manifest_dict()
    manifest["cases"][0] = bad_case
    with pytest.raises(ValueError):
        cs.validate_manifest(manifest)


@pytest.mark.parametrize("field", ["id", "gold"])
@pytest.mark.parametrize("value", [[], {}])
def test_manifest_rejects_non_string_identifiers_and_labels(field, value):
    manifest = _manifest_dict()
    manifest["cases"][0][field] = value
    with pytest.raises(ValueError):
        cs.validate_manifest(manifest)


@pytest.mark.parametrize("field", ["status", "prediction"])
def test_recorded_row_rejects_non_string_enums(field):
    row = _recorded_dict()["rows"][0]
    row[field] = []
    with pytest.raises(ValueError):
        cs.validate_row(row, {c["id"] for c in _manifest_dict()["cases"]})


def test_recorded_revision_rejects_non_string(tmp_path):
    recorded = _recorded_dict()
    recorded["recorded_source_revision"] = 42
    with pytest.raises(ValueError):
        _run_with(tmp_path, recorded=recorded)


def test_grammar_source_drift_cannot_introduce_output_side_effects():
    original = TEXTLOOP.read_text()
    source = original.replace(
        "def _confirmation_reply_kind(normalized: str) -> str | None:\n",
        "def _confirmation_reply_kind(normalized: str) -> str | None:\n    print('unexpected')\n",
    )
    assert source != original
    ast.parse(source)
    with pytest.raises(ValueError, match="source drift"):
        cs.load_baseline(source, REALTIME.read_text())


# --- 1. acceptance totals from the committed fixtures ------------------------


def test_committed_manifest_bytes_match_recorded_protocol_hash():
    assert _sha256(MANIFEST) == RECORDED_MANIFEST_SHA256
    assert _recorded_dict()["manifest_sha256"] == RECORDED_MANIFEST_SHA256


def test_default_replay_reproduces_study_totals():
    result = _run_with(Path("/nonexistent-unused"))
    summary = result["summary"]
    assert summary["manifest_cases"] == 18
    assert summary["attempted"] == 18
    assert summary["not_attempted"] == 0
    assert summary["usable"] == 18
    assert summary["errors_no_output"] == 0
    assert summary["baseline_correct"] == 14
    assert summary["recorded_correct"] == 17
    assert summary["unsafe_confirms"] == 0
    assert summary["recovered_baseline_deferrals"] == 4
    assert summary["recorded_match_all_manifest"] == pytest.approx(17 / 18)
    assert summary["recorded_match_attempted"] == pytest.approx(17 / 18)
    assert summary["http_ms"]["count"] == 18
    assert result["recorded_not_live"] is True
    assert result["live"] is False
    assert result["model_expected"] == "jev-1.13.0"


def test_mixed_task_disagreement_is_preserved_not_relabeled():
    rows = _rows_by_id(_run_with(Path("/nonexistent-unused")))
    row = rows["different_task"]
    assert row["gold"] == "defer"
    assert row["baseline"] == "defer"
    assert row["prediction"] == "cancel"
    assert row["recorded_correct"] is False
    assert row["unsafe_confirm"] is False
    summary_disagreements = _run_with(Path("/nonexistent-unused"))["summary"]["disagreements"]
    assert [d["case_id"] for d in summary_disagreements] == ["different_task"]


def test_recoveries_are_the_four_confirm_cancel_baseline_deferrals():
    rows = _rows_by_id(_run_with(Path("/nonexistent-unused")))
    recovered = sorted(cid for cid, row in rows.items() if row["recovered"])
    assert recovered == ["changed_mind", "expanded_assent", "explicit_rejection", "natural_assent"]
    for cid in recovered:
        assert rows[cid]["baseline"] == "defer"
        assert rows[cid]["gold"] in {"confirm", "cancel"}
        assert rows[cid]["prediction"] == rows[cid]["gold"]
    # A correctly deferred gold-defer case is never a recovery.
    assert rows["time_revision"]["recovered"] is False


def test_nearest_rank_percentiles_from_parsed_rows():
    result = _run_with(Path("/nonexistent-unused"))
    values = sorted(r["http_elapsed_ms"] for r in _recorded_dict()["rows"] if r["status"] == "ok")
    assert result["summary"]["http_ms"]["p50"] == values[math.ceil(0.5 * len(values)) - 1]
    assert result["summary"]["http_ms"]["p95"] == values[math.ceil(0.95 * len(values)) - 1]
    assert "caveat" in result["summary"]["latency_caveat"].lower() or "connection" in result["summary"]["latency_caveat"]


def test_confidence_is_recorded_but_never_decides_correctness():
    rows = _rows_by_id(_run_with(Path("/nonexistent-unused")))
    # The one wrong recorded answer carries the lowest confidence, but a
    # high-confidence wrong answer would be just as wrong: correctness is
    # gold equality only.
    assert rows["different_task"]["confidence"] == pytest.approx(0.49)
    assert rows["different_task"]["recorded_correct"] is False


def test_low_confidence_correct_answer_still_counts(tmp_path):
    recorded = _recorded_dict()
    for row in recorded["rows"]:
        if row["case_id"] == "bare_yes":
            row["confidence"] = 0.01
    rows = _rows_by_id(_run_with(tmp_path, recorded=recorded))
    assert rows["bare_yes"]["recorded_correct"] is True
    assert rows["bare_yes"]["confidence"] == pytest.approx(0.01)


# --- 2. baseline comes from trusted source, not literal expectations ---------


def test_baseline_uses_realtime_normalization_and_textloop_grammar():
    baseline = cs.load_baseline(TEXTLOOP.read_text(), REALTIME.read_text())
    assert baseline("Yes!") == "confirm"
    assert baseline("No, thanks.") == "cancel"
    assert baseline("yes   yes") == "confirm"
    assert baseline("yes but tomorrow") == "defer"
    assert baseline("") == "defer"


def test_baseline_extraction_executes_source_constants_not_literals():
    source = TEXTLOOP.read_text()
    mutated = source.replace('CONFIRM_YES_PHRASES = {\n    "yes",', 'CONFIRM_YES_PHRASES = {\n    "affirmative",', 1)
    assert mutated != source
    mutated = mutated.replace('_CONFIRM_YES_LEADS = {"yes",', '_CONFIRM_YES_LEADS = {"affirmative",', 1)
    baseline = cs.load_baseline(mutated, REALTIME.read_text())
    assert baseline("yes") == "defer"
    assert baseline("affirmative") == "confirm"


def test_baseline_uses_the_source_normalization_statements():
    realtime = REALTIME.read_text()
    marker = '        normalized = _re.sub(r"\\s+", " ", normalized)\n'
    assert marker in realtime
    mutated = realtime.replace(marker, '        normalized = _re.sub(r"\\s+", "_", normalized)\n', 1)
    baseline = cs.load_baseline(TEXTLOOP.read_text(), mutated)
    assert baseline("yes yes") == "defer"  # "yes_yes" is not in the grammar


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("def _confirmation_reply_kind(", "def _confirmation_reply_kind_v2(", 1),
        lambda s: s + "\n\ndef _confirmation_reply_kind(normalized):\n    return None\n",
        lambda s: s.replace("_CONFIRM_YES_LEADS = {", "_CONFIRM_YES_LEADS_X = {", 1),
        lambda s: s.replace("CONFIRM_NO_PHRASES = {\n", "CONFIRM_NO_PHRASES = frozenset({\n", 1).replace(
            '    "no thank you",\n}\n', '    "no thank you",\n})\n', 1
        ),
    ],
    ids=["renamed-function", "duplicate-function", "missing-grammar-set", "non-literal-grammar-set"],
)
def test_textloop_source_drift_raises_clear_error(mutate):
    mutated = mutate(TEXTLOOP.read_text())
    assert mutated != TEXTLOOP.read_text()
    ast.parse(mutated)  # the drift must be valid Python, not a syntax error
    with pytest.raises(ValueError, match=r"grammar"):
        cs.load_baseline(mutated, REALTIME.read_text())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace(
            '        normalized = _re.sub(r"\\s+", " ", normalized)\n',
            '        normalized = _re.sub(r"\\s+", " ", normalized)\n        normalized = normalized.strip()\n',
            1,
        ),
        lambda s: s.replace('        normalized = _re.sub(r"\\s+", " ", normalized)\n', "", 1),
        lambda s: s.replace(
            '        normalized = _re.sub(r"\\s+", " ", normalized)\n',
            "        normalized = normalized.upper()\n",
            1,
        ),
        lambda s: s.replace("async def _maybe_resolve_confirmation(", "async def _maybe_resolve_confirmation_v2(", 1),
    ],
    ids=["three-assigns", "one-assign", "not-a-re-sub-call", "renamed-method"],
)
def test_realtime_normalization_drift_raises_clear_error(mutate):
    mutated = mutate(REALTIME.read_text())
    assert mutated != REALTIME.read_text()
    ast.parse(mutated)  # the drift must be valid Python, not a syntax error
    with pytest.raises(ValueError, match=r"normalization source drift"):
        cs.load_baseline(TEXTLOOP.read_text(), mutated)


# --- 3. manifest integrity ---------------------------------------------------


def test_changing_one_gold_label_hard_errors(tmp_path):
    manifest = _manifest_dict()
    for case in manifest["cases"]:
        if case["id"] == "different_task":
            case["gold"] = "cancel"
    with pytest.raises(ValueError, match=r"manifest.*sha256|sha256.*manifest"):
        _run_with(tmp_path, manifest=manifest)


def test_reserialized_manifest_with_same_content_still_hard_errors(tmp_path):
    # Byte equality, not semantic equality: indentation changes are drift.
    path = _write(tmp_path, "manifest.json", json.dumps(_manifest_dict(), indent=2))
    with pytest.raises(ValueError, match=r"sha256"):
        cs.run(manifest_path=path, recorded_path=RECORDED, repo_root=REPO)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda m: m["cases"].append(dict(m["cases"][0])), r"duplicate"),
        (lambda m: m.__setitem__("synthetic", False), r"synthetic"),
        (lambda m: m["cases"][0].__setitem__("gold", "execute"), r"label"),
        (lambda m: m["cases"].extend({"id": f"x{i}", "utterance": "yes", "gold": "confirm"} for i in range(3)), r"1\.\.20|count"),
    ],
    ids=["duplicate-id", "synthetic-marker", "bad-label", "over-count"],
)
def test_invalid_manifest_rejected_before_hash_comparison(mutate, message):
    manifest = _manifest_dict()
    mutate(manifest)
    with pytest.raises(ValueError, match=message):
        cs.validate_manifest(manifest)


# --- 4. recorded-row schema ---------------------------------------------------


def _mutated_recorded(case_id: str, **changes) -> dict:
    recorded = _recorded_dict()
    for row in recorded["rows"]:
        if row["case_id"] == case_id:
            for key, value in changes.items():
                if value is cs.ABSENT:
                    row.pop(key, None)
                else:
                    row[key] = value
    return recorded


@pytest.mark.parametrize("key", ["gold", "baseline", "label", "correct", "utterance", "latency_ms", "timestamp"])
def test_unknown_row_keys_including_gold_and_baseline_are_rejected(tmp_path, key):
    recorded = _mutated_recorded("bare_yes", **{key: "confirm"})
    with pytest.raises(ValueError, match=r"unknown.*key|key.*unknown"):
        _run_with(tmp_path, recorded=recorded)


@pytest.mark.parametrize("confidence", [True, math.nan, math.inf, -0.1, 1.1, "0.8", None])
def test_invalid_confidence_rejected(tmp_path, confidence):
    recorded = _mutated_recorded("bare_yes", confidence=confidence)
    with pytest.raises(ValueError, match=r"confidence"):
        _run_with(tmp_path, recorded=recorded)


@pytest.mark.parametrize("elapsed", [-1.0, math.nan, math.inf, True, "300", None])
def test_invalid_elapsed_ms_rejected(tmp_path, elapsed):
    recorded = _mutated_recorded("bare_yes", http_elapsed_ms=elapsed)
    with pytest.raises(ValueError, match=r"http_elapsed_ms"):
        _run_with(tmp_path, recorded=recorded)


def test_missing_required_ok_row_field_rejected(tmp_path):
    recorded = _mutated_recorded("bare_yes", http_elapsed_ms=cs.ABSENT)
    with pytest.raises(ValueError, match=r"http_elapsed_ms"):
        _run_with(tmp_path, recorded=recorded)


def test_wrong_model_row_rejected_against_frozen_protocol(tmp_path):
    recorded = _mutated_recorded("bare_yes", model="jev-1.14.0")
    with pytest.raises(ValueError, match=r"model"):
        _run_with(tmp_path, recorded=recorded)


def test_recorded_metadata_cannot_redefine_expected_model(tmp_path):
    recorded = _recorded_dict()
    recorded["model"] = "jev-1.14.0"
    for row in recorded["rows"]:
        row["model"] = "jev-1.14.0"
    with pytest.raises(ValueError, match=r"model"):
        _run_with(tmp_path, recorded=recorded)


def test_unknown_prediction_label_rejected(tmp_path):
    recorded = _mutated_recorded("bare_yes", prediction="execute")
    with pytest.raises(ValueError, match=r"prediction|label"):
        _run_with(tmp_path, recorded=recorded)


def test_duplicate_case_id_rejected(tmp_path):
    recorded = _recorded_dict()
    recorded["rows"].append(dict(recorded["rows"][0]))
    with pytest.raises(ValueError, match=r"duplicate"):
        _run_with(tmp_path, recorded=recorded)


def test_extra_case_id_not_in_manifest_rejected(tmp_path):
    recorded = _recorded_dict()
    extra = dict(recorded["rows"][0])
    extra["case_id"] = "not_in_manifest"
    recorded["rows"].append(extra)
    with pytest.raises(ValueError, match=r"not_in_manifest|unknown case"):
        _run_with(tmp_path, recorded=recorded)


def test_unknown_status_rejected_not_attempted_is_derived_only(tmp_path):
    recorded = _mutated_recorded("bare_yes", status="not_attempted")
    with pytest.raises(ValueError, match=r"status"):
        _run_with(tmp_path, recorded=recorded)


def test_recorded_not_live_marker_required(tmp_path):
    recorded = _recorded_dict()
    recorded["recorded_not_live"] = False
    with pytest.raises(ValueError, match=r"recorded_not_live"):
        _run_with(tmp_path, recorded=recorded)


def test_embedded_template_text_must_match_recorded_template_hash(tmp_path):
    recorded = _recorded_dict()
    assert hashlib.sha256(recorded["request_template_text"].encode()).hexdigest() == RECORDED_TEMPLATE_SHA256
    recorded["request_template_text"] = recorded["request_template_text"].replace("Do not execute anything.", "Execute.")
    with pytest.raises(ValueError, match=r"template"):
        _run_with(tmp_path, recorded=recorded)


def test_error_row_has_no_prediction_confidence_or_latency(tmp_path):
    recorded = _mutated_recorded(
        "bare_yes",
        status="error",
        error_type="TimeoutError",
        prediction=cs.ABSENT,
        confidence=cs.ABSENT,
        http_elapsed_ms=cs.ABSENT,
        model=cs.ABSENT,
    )
    result = _run_with(tmp_path, recorded=recorded)
    row = _rows_by_id(result)["bare_yes"]
    assert row["status"] == "error"
    assert row["prediction"] is None
    assert row["recorded_correct"] is None
    assert row["http_elapsed_ms"] is None
    assert row["usage"] == {"input_tokens": 491, "output_tokens": 39}
    summary = result["summary"]
    assert summary["attempted"] == 18
    assert summary["usable"] == 17
    assert summary["errors_no_output"] == 1
    assert summary["recorded_correct"] == 16
    assert summary["http_ms"]["count"] == 17
    assert summary["recorded_match_all_manifest"] == pytest.approx(16 / 18)
    assert summary["recorded_match_attempted"] == pytest.approx(16 / 18)


def test_error_row_with_prediction_or_latency_rejected(tmp_path):
    recorded = _mutated_recorded("bare_yes", status="error", error_type="TimeoutError")
    with pytest.raises(ValueError, match=r"error row"):
        _run_with(tmp_path, recorded=recorded)


def test_missing_rows_stay_in_manifest_denominator_as_not_attempted(tmp_path):
    recorded = _recorded_dict()
    recorded["rows"] = [r for r in recorded["rows"] if r["case_id"] not in {"bare_yes", "silence"}]
    result = _run_with(tmp_path, recorded=recorded)
    rows = _rows_by_id(result)
    assert rows["bare_yes"]["status"] == "not_attempted"
    assert rows["bare_yes"]["prediction"] is None
    assert rows["bare_yes"]["recorded_correct"] is None
    assert rows["bare_yes"]["baseline"] == "confirm"  # baseline still computed
    summary = result["summary"]
    assert summary["manifest_cases"] == 18
    assert summary["attempted"] == 16
    assert summary["not_attempted"] == 2
    assert summary["recorded_correct"] == 15
    assert summary["recorded_match_all_manifest"] == pytest.approx(15 / 18)
    assert summary["recorded_match_attempted"] == pytest.approx(15 / 16)
    assert summary["http_ms"]["count"] == 16


def test_no_recorded_rows_reports_no_accuracy(tmp_path):
    recorded = _recorded_dict()
    recorded["rows"] = []
    summary = _run_with(tmp_path, recorded=recorded)["summary"]
    assert summary["attempted"] == 0
    assert summary["not_attempted"] == 18
    assert summary["baseline_correct"] == 14
    assert summary["recorded_match_all_manifest"] is None
    assert summary["recorded_match_attempted"] is None
    assert summary["http_ms"] == {"count": 0, "p50": None, "p95": None}


def test_unsafe_confirm_is_confirm_prediction_on_non_confirm_gold(tmp_path):
    recorded = _mutated_recorded("quoted_other", prediction="confirm")
    result = _run_with(tmp_path, recorded=recorded)
    assert _rows_by_id(result)["quoted_other"]["unsafe_confirm"] is True
    assert result["summary"]["unsafe_confirms"] == 1
    assert result["summary"]["recorded_correct"] == 16


def test_usage_must_be_numeric_token_counts(tmp_path):
    recorded = _mutated_recorded("bare_yes", usage={"input_tokens": "491"})
    with pytest.raises(ValueError, match=r"usage"):
        _run_with(tmp_path, recorded=recorded)


# --- 5. provenance ---------------------------------------------------------------


def test_provenance_hashes_sources_and_matches_recorded_flag():
    result = _run_with(Path("/nonexistent-unused"))
    prov = result["provenance"]
    assert prov["source_sha256"]["textloop"] == _sha256(TEXTLOOP)
    assert prov["source_sha256"]["realtime"] == _sha256(REALTIME)
    assert prov["manifest_sha256"] == RECORDED_MANIFEST_SHA256
    assert prov["recorded_source_sha256"] == _recorded_dict()["baseline_source_sha256"]
    assert prov["baseline_sources_match_recorded"] == (
        prov["source_sha256"] == prov["recorded_source_sha256"]
    )
    assert prov["recorded_source_revision"] == _recorded_dict()["recorded_source_revision"]
    assert prov["recorded_source_revision"].startswith("f5d5f43")
    # In a checkout the current revision is reported, never hard-coded.
    assert prov["source_revision"] is None or len(prov["source_revision"]) == 40
    assert prov["dirty"] in (None, True, False)


def test_git_unavailable_reports_null_provenance_not_fabricated(monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(cs.subprocess, "run", unavailable)
    prov = cs.git_provenance(REPO)
    assert prov == {"source_revision": None, "dirty": None}
    result = _run_with(Path("/nonexistent-unused"))
    assert result["provenance"]["source_revision"] is None
    assert result["provenance"]["dirty"] is None
    assert result["provenance"]["source_sha256"]["textloop"] == _sha256(TEXTLOOP)


def test_baseline_source_mismatch_is_reported_not_fatal(tmp_path):
    recorded = _recorded_dict()
    recorded["baseline_source_sha256"]["textloop"] = "0" * 64
    result = _run_with(tmp_path, recorded=recorded)
    assert result["provenance"]["baseline_sources_match_recorded"] is False
    assert result["summary"]["baseline_correct"] == 14


# --- 6. CLI ---------------------------------------------------------------------


def test_cli_refuses_to_overwrite_existing_output(tmp_path, capsys):
    output = tmp_path / "result.json"
    output.write_text("{}")
    with pytest.raises(FileExistsError):
        cs.main(["--output", str(output)])
    assert output.read_text() == "{}"


def test_cli_writes_output_once_and_prints_json(tmp_path, capsys):
    output = tmp_path / "result.json"
    assert cs.main(["--output", str(output)]) == 0
    printed = json.loads(capsys.readouterr().out)
    written = json.loads(output.read_text())
    assert printed["summary"] == written["summary"]
    assert printed["summary"]["recorded_correct"] == 17


def test_cli_default_replay_makes_no_network_connection(tmp_path):
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    sentinel = tmp_path / "sitecustomize-loaded"
    (guard_dir / "sitecustomize.py").write_text(
        "import socket, pathlib\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('loaded')\n"
        "def _refuse(*args, **kwargs):\n"
        "    raise RuntimeError('network refused by test sitecustomize')\n"
        "socket.socket.connect = _refuse\n"
        "socket.create_connection = _refuse\n"
        "socket.getaddrinfo = _refuse\n"
    )
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(guard_dir), "HOME": str(tmp_path)}

    # The guard really bites: a plain connection attempt fails under it.
    probe = subprocess.run(
        [sys.executable, "-c", "import socket; socket.create_connection(('127.0.0.1', 9))"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert probe.returncode != 0
    assert "network refused by test sitecustomize" in probe.stderr
    assert sentinel.read_text() == "loaded"
    sentinel.unlink()

    completed = subprocess.run(
        [sys.executable, str(REPO / "benchmark" / "confirmation_shadow.py")],
        env=env, capture_output=True, text=True, timeout=120, cwd=str(tmp_path),
    )
    assert sentinel.read_text() == "loaded", "isolated sitecustomize was not loaded before the CLI"
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["summary"]["manifest_cases"] == 18
    assert result["summary"]["baseline_correct"] == 14
    assert result["summary"]["recorded_correct"] == 17
    assert result["summary"]["recovered_baseline_deferrals"] == 4
    assert result["summary"]["unsafe_confirms"] == 0
    assert result["live"] is False


def test_module_has_no_network_credential_or_operations_paths():
    source = (REPO / "benchmark" / "confirmation_shadow.py").read_text()
    for forbidden in ("urllib", "requests", "httpx", "socket", "dotenv", "TYPESAFE_API_KEY", "Operations", ".env"):
        assert forbidden not in source, forbidden
    assert socket is not None  # imported only by this test's guard probe
