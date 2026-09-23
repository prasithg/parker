"""Offline confirmation-shadow replay: deterministic grammar vs. a recorded run.

Replays a frozen synthetic confirmation manifest through two arms and scores
both against the manifest's gold labels:

* **baseline** — the trusted repository grammar, executed from the AST of
  ``backend/app/conversation/textloop.py`` (``_confirmation_reply_kind`` and
  its six literal grammar sets) after the exact two inline normalization
  statements from ``RealtimeBridge._maybe_resolve_confirmation`` in
  ``backend/app/parker/realtime.py``. Nothing is imported from the app, so no
  config, database or provider side effects run. If the source structure
  drifts, loading fails with a clear ``ValueError`` instead of guessing.
* **recorded** — historical parsed provider responses from one bounded study,
  stored as a fixture. Replaying them is not a provider call: this module has
  no network, SDK or credential path, and is not connected to any executor.

Usage (from the repository root):

    python3 benchmark/confirmation_shadow.py                 # JSON to stdout
    python3 benchmark/confirmation_shadow.py --output out.json   # refuses to overwrite
"""

from __future__ import annotations

import argparse
import ast

import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "benchmark" / "fixtures"
DEFAULT_MANIFEST = FIXTURES / "confirmation_shadow_synthetic_v1.json"
DEFAULT_RECORDED = FIXTURES / "confirmation_shadow_jev_recorded_v1.json"
SOURCE_PATHS = {
    "textloop": Path("backend/app/conversation/textloop.py"),
    "realtime": Path("backend/app/parker/realtime.py"),
}

# Frozen protocol values. The recorded fixture may restate them but cannot
# redefine them.
MODEL = "jev-1.13.0"
LABELS = frozenset({"confirm", "cancel", "defer"})
GRAMMAR_BUILTINS = {"len": len, "all": all, "str": str}
ROW_STATUSES = frozenset({"ok", "error"})
ROW_KEYS = frozenset(
    {"case_id", "status", "prediction", "model", "confidence", "http_elapsed_ms", "usage", "error_type"}
)
OK_ROW_REQUIRED = frozenset({"case_id", "status", "prediction", "model", "confidence", "http_elapsed_ms"})
ERROR_ROW_FORBIDDEN = frozenset({"prediction", "confidence", "http_elapsed_ms"})
RECORDED_KEYS = frozenset(
    {
        "fixture_id", "kind", "recorded_not_live", "provider", "model", "endpoint", "recorded_on",
        "recorded_source_revision", "manifest_sha256", "request_template_sha256",
        "request_template_text", "baseline_source_sha256", "notes", "rows",
    }
)
RECORDED_REQUIRED = frozenset(
    {
        "recorded_not_live", "model", "manifest_sha256", "request_template_sha256",
        "request_template_text", "baseline_source_sha256", "rows",
    }
)

GRAMMAR_FUNCTION = "_confirmation_reply_kind"
GRAMMAR_SET_NAMES = frozenset(
    {
        "CONFIRM_YES_PHRASES", "CONFIRM_NO_PHRASES", "_CONFIRM_NO_LEADS",
        "_CONFIRM_NO_TOKENS", "_CONFIRM_YES_LEADS", "_CONFIRM_YES_TOKENS",
    }
)
NORMALIZATION_METHOD = "_maybe_resolve_confirmation"
NORMALIZATION_TARGET = "normalized"
NORMALIZATION_INPUT = "transcript"
NORMALIZATION_NAMES = frozenset({"_re", NORMALIZATION_INPUT, NORMALIZATION_TARGET})

LATENCY_CAVEAT = (
    "client HTTP elapsed over fresh connections includes connection overhead and is not "
    "server inference time; tiny n, nearest-rank percentiles, not a provider benchmark"
)
POLICY = (
    "offline replay of a recorded run; no action authority, no production integration, "
    "no clinical or generalization inference from a tiny synthetic cohort"
)

ABSENT = object()  # sentinel for tests that remove a row key
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- manifest ------------------------------------------------------------------


def validate_manifest(manifest: Any) -> list[dict]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("manifest must be an object with a cases list")
    cases = manifest["cases"]
    if not 1 <= len(cases) <= 20:
        raise ValueError("manifest case count outside 1..20")
    if manifest.get("synthetic") is not True:
        raise ValueError("synthetic manifest required")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("manifest case must be an object")
        if not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("manifest case id must be a non-empty string")
        if case["id"] in ids:
            raise ValueError("duplicate case id in manifest")
        ids.add(case["id"])
        if not isinstance(case.get("gold"), str) or case["gold"] not in LABELS or not isinstance(case.get("utterance"), str):
            raise ValueError(f"invalid label or utterance in manifest case {case.get('id')!r}")
    return cases


def load_manifest(path: Path) -> tuple[dict, str]:
    """Return (manifest, sha256 of the exact bytes on disk)."""
    data = Path(path).read_bytes()
    manifest = json.loads(data)
    validate_manifest(manifest)
    return manifest, sha256_bytes(data)


# --- trusted baseline grammar, executed from source AST -----------------------


def _loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _stored_names(node: ast.AST) -> set[str]:
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            names.add(arg.arg)
        for arg in (args.vararg, args.kwarg):
            if arg is not None:
                names.add(arg.arg)
    return names


def _extract_grammar(source: str) -> tuple[dict, ast.FunctionDef]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"grammar source does not parse: {exc}") from None
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == GRAMMAR_FUNCTION]
    if len(functions) != 1:
        raise ValueError(
            f"grammar source drift: expected exactly one module-level {GRAMMAR_FUNCTION}, found {len(functions)}"
        )
    scope: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in GRAMMAR_SET_NAMES:
                if target.id in scope:
                    raise ValueError(f"grammar source drift: {target.id} assigned more than once")
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    raise ValueError(f"grammar constant {target.id} is not a literal") from None
                if not isinstance(value, (set, frozenset)) or not all(isinstance(v, str) for v in value):
                    raise ValueError(f"grammar constant {target.id} is not a literal set of strings")
                scope[target.id] = value
    missing = GRAMMAR_SET_NAMES - set(scope)
    if missing:
        raise ValueError(f"grammar source drift: missing literal grammar sets {sorted(missing)}")
    fn = functions[0]
    external = _loaded_names(fn) - _stored_names(fn) - GRAMMAR_SET_NAMES - set(GRAMMAR_BUILTINS)
    if external:
        raise ValueError(f"grammar source drift: {GRAMMAR_FUNCTION} references {sorted(external)}")
    return scope, fn


def _is_re_sub_call(node: ast.AST) -> bool:
    """True if a ``_re.sub(<str>, <str>, ...)`` call appears anywhere in node."""
    for call in ast.walk(node):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "sub"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "_re"
            and len(call.args) == 3
            and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in call.args[:2])
        ):
            return True
    return False


def _extract_normalization(source: str) -> list[ast.Assign]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"normalization source does not parse: {exc}") from None
    methods = [
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == NORMALIZATION_METHOD
    ]
    if len(methods) != 1:
        raise ValueError(
            f"normalization source drift: expected exactly one async {NORMALIZATION_METHOD}, found {len(methods)}"
        )
    statements = [
        n
        for n in methods[0].body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == NORMALIZATION_TARGET for t in n.targets)
    ]
    if len(statements) != 2:
        raise ValueError(
            f"normalization source drift: expected exactly two inline '{NORMALIZATION_TARGET} = ...' "
            f"statements in {NORMALIZATION_METHOD}, found {len(statements)}"
        )
    expected_inputs = [{NORMALIZATION_INPUT}, {NORMALIZATION_TARGET}]
    for index, (stmt, expected) in enumerate(zip(statements, expected_inputs), start=1):
        if len(stmt.targets) != 1 or not isinstance(stmt.value, ast.Call) or not _is_re_sub_call(stmt.value):
            raise ValueError(
                f"normalization source drift: statement {index} is not a single-target _re.sub call pipeline"
            )
        loaded = _loaded_names(stmt.value)
        if not loaded <= NORMALIZATION_NAMES or not expected <= loaded:
            raise ValueError(
                f"normalization source drift: statement {index} reads {sorted(loaded)}, expected {sorted(expected)}"
            )
    return statements


def load_baseline(grammar_source: str, normalization_source: str) -> Callable[[str], str]:
    """Build ``classify(utterance) -> confirm|cancel|defer`` from trusted source text.

    Only the pinned pure nodes are executed: the grammar function with its six
    literal sets, and the two inline normalization assignments. No module
    import happens, so no app config, database or provider code runs.
    """
    scope, fn = _extract_grammar(grammar_source)
    statements = _extract_normalization(normalization_source)
    scope["__builtins__"] = GRAMMAR_BUILTINS
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<pinned-confirmation-grammar>", "exec"), scope)
    normalize = compile(ast.Module(body=statements, type_ignores=[]), "<pinned-realtime-normalization>", "exec")
    grammar = scope[GRAMMAR_FUNCTION]
    outcome = {"yes": "confirm", "no": "cancel", None: "defer"}

    def classify(utterance: str) -> str:
        local: dict[str, Any] = {"_re": re, NORMALIZATION_INPUT: utterance}
        exec(normalize, local)
        return outcome[grammar(local[NORMALIZATION_TARGET])]

    return classify


# --- recorded provider rows ------------------------------------------------------


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_usage(case_id: str, usage: Any) -> None:
    if not isinstance(usage, dict):
        raise ValueError(f"invalid usage for case {case_id!r}: must be an object of token counts")
    for key, value in usage.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"invalid usage for case {case_id!r}: {key!r} must be a non-negative integer")


def validate_row(row: Any, manifest_ids: set[str]) -> dict:
    if not isinstance(row, dict):
        raise ValueError("recorded row must be an object")
    unknown = set(row) - ROW_KEYS
    if unknown:
        raise ValueError(f"unknown recorded row key(s) {sorted(unknown)}; allowed keys are {sorted(ROW_KEYS)}")
    case_id = row.get("case_id")
    if not isinstance(case_id, str) or case_id not in manifest_ids:
        raise ValueError(f"unknown case_id {case_id!r}: not in manifest")
    status = row.get("status")
    if not isinstance(status, str) or status not in ROW_STATUSES:
        raise ValueError(f"invalid status {status!r} for case {case_id!r}; recorded rows are ok|error")
    if "model" in row and row["model"] != MODEL:
        raise ValueError(f"model mismatch for case {case_id!r}: expected {MODEL}, got {row['model']!r}")
    if "usage" in row:
        _validate_usage(case_id, row["usage"])
    if status == "ok":
        missing = OK_ROW_REQUIRED - set(row)
        if missing:
            raise ValueError(f"ok row for case {case_id!r} missing {sorted(missing)}")
        if "error_type" in row:
            raise ValueError(f"ok row for case {case_id!r} must not carry error_type")
        if not isinstance(row["prediction"], str) or row["prediction"] not in LABELS:
            raise ValueError(f"invalid prediction label {row['prediction']!r} for case {case_id!r}")
        confidence = row["confidence"]
        if not _is_finite_number(confidence) or not 0 <= confidence <= 1:
            raise ValueError(f"invalid confidence for case {case_id!r}: must be finite in [0, 1]")
        elapsed = row["http_elapsed_ms"]
        if not _is_finite_number(elapsed) or elapsed < 0:
            raise ValueError(f"invalid http_elapsed_ms for case {case_id!r}: must be finite and non-negative")
    else:
        present = ERROR_ROW_FORBIDDEN & set(row)
        if present:
            raise ValueError(f"error row for case {case_id!r} must not carry {sorted(present)}")
        if "error_type" in row and (not isinstance(row["error_type"], str) or not row["error_type"]):
            raise ValueError(f"invalid error_type for case {case_id!r}")
    return row


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ValueError(f"{label} must be a lowercase hex sha256")
    return value


def load_recorded(path: Path, manifest_sha256: str, manifest_ids: set[str]) -> tuple[dict, str]:
    """Load and validate the recorded fixture against the frozen protocol.

    The manifest hash must equal the hash recorded before the original run,
    so any change to a gold label (or even re-serialization) hard-errors.
    """
    data = Path(path).read_bytes()
    recorded = json.loads(data)
    if not isinstance(recorded, dict):
        raise ValueError("recorded fixture must be an object")
    unknown = set(recorded) - RECORDED_KEYS
    if unknown:
        raise ValueError(f"unknown recorded fixture key(s) {sorted(unknown)}")
    missing = RECORDED_REQUIRED - set(recorded)
    if missing:
        raise ValueError(f"recorded fixture missing {sorted(missing)}")
    if recorded["recorded_not_live"] is not True:
        raise ValueError("recorded_not_live must be true: this fixture is a historical record, never a live run")
    if recorded["model"] != MODEL:
        raise ValueError(f"recorded model must be the frozen protocol model {MODEL}")
    expected_manifest = _require_sha256(recorded["manifest_sha256"], "manifest_sha256")
    if expected_manifest != manifest_sha256:
        raise ValueError(
            "manifest sha256 mismatch: loaded manifest bytes differ from the manifest frozen before the "
            "recorded run; gold labels may not change under a recorded result"
        )
    template_sha = _require_sha256(recorded["request_template_sha256"], "request_template_sha256")
    text = recorded["request_template_text"]
    if not isinstance(text, str) or sha256_bytes(text.encode("utf-8")) != template_sha:
        raise ValueError("request template text sha256 mismatch against the recorded template hash")
    sources = recorded["baseline_source_sha256"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_PATHS):
        raise ValueError(f"baseline_source_sha256 must have exactly {sorted(SOURCE_PATHS)}")
    for name, value in sources.items():
        _require_sha256(value, f"baseline_source_sha256.{name}")
    if "recorded_source_revision" in recorded and (
        not isinstance(recorded["recorded_source_revision"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", recorded["recorded_source_revision"])
    ):
        raise ValueError("recorded_source_revision must be a full git sha")
    if not isinstance(recorded["rows"], list):
        raise ValueError("recorded rows must be a list")
    seen: set[str] = set()
    for row in recorded["rows"]:
        validate_row(row, manifest_ids)
        if row["case_id"] in seen:
            raise ValueError(f"duplicate case_id {row['case_id']!r} in recorded rows")
        seen.add(row["case_id"])
    return recorded, sha256_bytes(data)


# --- replay and scoring --------------------------------------------------------------


def nearest_rank(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def replay(manifest: dict, recorded: dict, baseline: Callable[[str], str]) -> dict:
    by_id = {row["case_id"]: row for row in recorded["rows"]}
    rows = []
    for case in manifest["cases"]:
        gold = case["gold"]
        rule = baseline(case["utterance"])
        recorded_row = by_id.get(case["id"])
        status = recorded_row["status"] if recorded_row else "not_attempted"
        prediction = recorded_row.get("prediction") if status == "ok" else None
        correct = (prediction == gold) if status == "ok" else None
        row = {
            "case_id": case["id"],
            "utterance": case["utterance"],
            "gold": gold,
            "baseline": rule,
            "baseline_correct": rule == gold,
            "recoverable": gold in {"confirm", "cancel"} and rule == "defer",
            "status": status,
            "prediction": prediction,
            "confidence": recorded_row.get("confidence") if status == "ok" else None,
            "model": recorded_row.get("model") if recorded_row else None,
            "http_elapsed_ms": recorded_row.get("http_elapsed_ms") if status == "ok" else None,
            "usage": recorded_row.get("usage") if recorded_row else None,
            "error_type": recorded_row.get("error_type") if status == "error" else None,
            "recorded_correct": correct,
            "unsafe_confirm": status == "ok" and prediction == "confirm" and gold != "confirm",
        }
        row["recovered"] = bool(row["recoverable"] and correct)
        if status == "not_attempted":
            row["note"] = "no recorded provider response for this manifest case"
        elif status == "error":
            row["note"] = "recorded attempt produced no usable output"
        rows.append(row)
    return {"rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    attempted = sum(r["status"] != "not_attempted" for r in rows)
    usable = sum(r["status"] == "ok" for r in rows)
    recorded_correct = sum(r["recorded_correct"] is True for r in rows)
    latencies = [r["http_elapsed_ms"] for r in rows if r["status"] == "ok"]
    return {
        "manifest_cases": total,
        "attempted": attempted,
        "not_attempted": total - attempted,
        "usable": usable,
        "errors_no_output": attempted - usable,
        "baseline_correct": sum(r["baseline_correct"] for r in rows),
        "baseline_match_all_manifest": (sum(r["baseline_correct"] for r in rows) / total) if total else None,
        "recorded_correct": recorded_correct,
        "recorded_match_all_manifest": (recorded_correct / total) if attempted else None,
        "recorded_match_attempted": (recorded_correct / attempted) if attempted else None,
        "unsafe_confirms": sum(r["unsafe_confirm"] for r in rows),
        "recovered_baseline_deferrals": sum(r["recovered"] for r in rows),
        "disagreements": [
            {"case_id": r["case_id"], "gold": r["gold"], "baseline": r["baseline"], "recorded": r["prediction"]}
            for r in rows
            if r["recorded_correct"] is False
        ],
        "http_ms": {
            "count": len(latencies),
            "p50": nearest_rank(latencies, 0.5),
            "p95": nearest_rank(latencies, 0.95),
        },
        "latency_caveat": LATENCY_CAVEAT,
        "denominator_note": (
            "all-manifest rates divide by every manifest case including not_attempted and error rows; "
            "attempted rates divide by recorded attempts (ok+error); correctness is gold equality only"
        ),
    }


# --- provenance ------------------------------------------------------------------------


def git_provenance(repo_root: Path) -> dict:
    """Current checkout revision and dirty flag, or nulls when git is unavailable."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=10, check=False
        )
        if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", head.stdout.strip()):
            return {"source_revision": None, "dirty": None}
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, timeout=10, check=False
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return {"source_revision": head.stdout.strip(), "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"source_revision": None, "dirty": None}


def run(
    manifest_path: Path = DEFAULT_MANIFEST,
    recorded_path: Path = DEFAULT_RECORDED,
    repo_root: Path = REPO_ROOT,
) -> dict:
    manifest, manifest_sha = load_manifest(manifest_path)
    sources = {name: (repo_root / rel).read_bytes() for name, rel in SOURCE_PATHS.items()}
    baseline = load_baseline(sources["textloop"].decode("utf-8"), sources["realtime"].decode("utf-8"))
    recorded, recorded_sha = load_recorded(recorded_path, manifest_sha, {c["id"] for c in manifest["cases"]})
    result = replay(manifest, recorded, baseline)
    source_sha = {name: sha256_bytes(data) for name, data in sources.items()}
    provenance = {
        **git_provenance(repo_root),
        "source_paths": {name: str(rel) for name, rel in SOURCE_PATHS.items()},
        "source_sha256": source_sha,
        "manifest_sha256": manifest_sha,
        "recorded_fixture_sha256": recorded_sha,
        "request_template_sha256": recorded["request_template_sha256"],
        "recorded_source_sha256": recorded["baseline_source_sha256"],
        "recorded_source_revision": recorded.get("recorded_source_revision"),
        "baseline_sources_match_recorded": source_sha == recorded["baseline_source_sha256"],
    }
    return {
        "kind": "confirmation_shadow_replay",
        "recorded_not_live": True,
        "live": False,
        "model_expected": MODEL,
        "policy": POLICY,
        "provenance": provenance,
        "summary": result["summary"],
        "rows": result["rows"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--recorded", type=Path, default=DEFAULT_RECORDED)
    parser.add_argument("--output", type=Path, default=None, help="write JSON here; refuses to overwrite")
    args = parser.parse_args(argv)
    if args.output is not None and args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing evidence: {args.output}")
    result = run(args.manifest, args.recorded)
    text = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        with open(args.output, "x", encoding="utf-8") as handle:
            handle.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
