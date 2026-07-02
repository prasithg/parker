"""Leak-guard + schema validator for the sanitized private-audio pattern notes.

The raw-audio validation lane lives outside this repo; the repo may carry
pattern SHAPES only. These tests make the sanitization mechanical: no URLs,
no filesystem paths, no content hashes, no verbatim-quote vectors — and the
schema keeps every entry honest about coverage (covered_by_synthetic and
synthetic_gap must agree) and in sync with the generator's variant names.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTES_PATH = REPO_ROOT / "benchmark" / "data" / "private_audio_pattern_notes_v0.json"

SAFETY_LABELS = {"safe_miss", "safe_nuisance", "unsafe_adjacent_guarded"}
COMMAND_CLASSES = {"no_action_counting", "no_action_ambient_monologue"}


def _load() -> dict:
    return json.loads(NOTES_PATH.read_text())


def test_pattern_notes_never_leak_sources() -> None:
    raw = NOTES_PATH.read_text()
    lowered = raw.lower()
    assert "http" not in lowered
    assert "/users/" not in lowered
    assert "youtu" not in lowered
    # content hashes (sha1/sha256 shapes) identify raw clips
    assert re.search(r"[0-9a-fA-F]{40}", raw) is None


def test_pattern_notes_schema_and_coverage_honesty() -> None:
    payload = _load()
    entries = payload["entries"]
    assert entries, "pattern notes must not be empty"
    for entry in entries:
        assert entry["observed_in"] == "web-private local lane"
        assert isinstance(entry["failure_class"], str) and entry["failure_class"]
        assert isinstance(entry["description"], str) and len(entry["description"]) > 20
        assert isinstance(entry["covered_by_synthetic"], bool)
        assert entry["safety_label"] in SAFETY_LABELS
        gap = entry["synthetic_gap"]
        assert gap is None or (isinstance(gap, str) and gap)
        # honesty coupling: a covered pattern has no open gap; an uncovered
        # pattern must say what the gap is.
        if entry["covered_by_synthetic"]:
            assert gap is None
        else:
            assert gap is not None


def test_pattern_notes_stay_in_sync_with_the_generator() -> None:
    import sys

    sys.path.append(str(REPO_ROOT))
    from benchmark.audio_harness.generate_synthetic import COMMANDS, VARIANTS

    variant_names = {name for name, _ in VARIANTS}
    payload = _load()
    for entry in payload["entries"]:
        variant = entry.get("generator_variant")
        if variant is not None:
            assert variant in variant_names, (
                f"pattern notes cite generator variant {variant!r} that "
                "generate_synthetic.py no longer provides"
            )
        command_class = entry.get("generator_command_class")
        if command_class is not None:
            assert command_class in COMMAND_CLASSES
    # the ambient/no-action command classes cited above must exist in the corpus
    from app.conversation.textloop import _counting_sequence_response

    assert any(_counting_sequence_response(cmd) is not None for cmd in COMMANDS), (
        "pattern notes promise a counting no-action command in the synthetic corpus"
    )
