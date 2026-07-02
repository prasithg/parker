"""Tests for the real-audio eval harness (benchmark/audio_harness).

No real audio and no faster-whisper: ASR is injected as a fake segment
transcriber, and audio "files" are throwaway temp bytes. What is pinned
here: outcome classification semantics, the cooperative repair
simulation, the ASR cache contract, and that reports never leak
filesystem paths.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from benchmark.audio_harness.asr import ASRConfig, ASRResult, CachedASR  # noqa: E402
from benchmark.audio_harness.manifest import Clip, load_clips  # noqa: E402
from benchmark.audio_harness.replay import Outcome, route_lines  # noqa: E402
from benchmark.audio_harness.score import (  # noqa: E402
    choice_matches,
    classify,
    intents_match,
    normalize_action,
    token_jaccard,
    wer,
)


def _clip(tmp_path: Path, *, sha: str = "a" * 64, oracle: str = "hello", **overrides) -> Clip:
    audio = tmp_path / f"{sha[:8]}.wav"
    audio.write_bytes(b"RIFF-fake")
    defaults = dict(
        sha256=sha,
        path=audio,
        dataset="test/fake-corpus",
        language="en",
        speaker_condition="dysarthric",
        provenance="public",
        oracle=oracle,
        duration_sec=1.0,
    )
    defaults.update(overrides)
    return Clip(**defaults)


# ---------------------------------------------------------------- scoring

def _outcome(effect_captured=None, kinds=None, selections=0):
    return Outcome(
        kinds=kinds or ([] if effect_captured else ["noop"]),
        captured=effect_captured or [],
        repair_selections=selections,
    )


REMINDER = {"requested_action": "reminder", "subject": "water the tomato plants", "recipient": None}


def test_exact_match_when_asr_path_captures_same_intent() -> None:
    clean = _outcome([REMINDER], kinds=["captured"])
    asr = _outcome([dict(REMINDER)], kinds=["captured"])
    verdict = classify(clean, asr, asr)
    assert verdict["lane"] == "intent"
    assert verdict["norepair"] == "exact"
    assert verdict["repair"] == "exact"


def test_repair_recovery_counts_only_with_a_selection() -> None:
    clean = _outcome([REMINDER], kinds=["captured"])
    missed = _outcome(None, kinds=["choices"])
    recovered = _outcome([dict(REMINDER)], kinds=["choices", "captured"], selections=1)
    verdict = classify(clean, missed, recovered)
    assert verdict["norepair"] == "safe_miss"
    assert verdict["repair"] == "repair_recovered"


def test_wrong_recipient_is_unsafe_capture() -> None:
    clean = _outcome(
        [{"requested_action": "family_message", "subject": "visit went well", "recipient": "Sarah"}],
        kinds=["captured"],
    )
    asr = _outcome(
        [{"requested_action": "family_message", "subject": "visit went well", "recipient": "Dave"}],
        kinds=["captured"],
    )
    verdict = classify(clean, asr, asr)
    assert verdict["norepair"] == "unsafe_capture"


def test_recipient_spelling_variant_is_the_same_person() -> None:
    # whisper-small hears "Tell Sarah" as "Tell Sara" — same person,
    # phonetic spelling. Must count as exact, never unsafe.
    clean = _outcome(
        [{"requested_action": "message", "subject": "message Sarah",
          "recipient": "Sarah", "intent_text": "physio went well today"}],
        kinds=["captured"],
    )
    asr = _outcome(
        [{"requested_action": "message", "subject": "message Sara",
          "recipient": "Sara", "intent_text": "Physio went well today."}],
        kinds=["captured"],
    )
    verdict = classify(clean, asr, asr)
    assert verdict["norepair"] == "exact"


def test_lost_recipient_is_wrong_content_not_unsafe() -> None:
    # ASR erased the name ("Tell Sarah..." -> "There a..."): a recipient-less
    # message cannot be misdirected — task failure, not safety failure. It
    # must also NOT count as recovered, even when a repair choice was taken.
    clean = _outcome(
        [{"requested_action": "message", "subject": "message Sarah", "recipient": "Sarah"}],
        kinds=["captured"],
    )
    asr = _outcome(
        [{"requested_action": "family_message", "subject": "There a physio went well", "recipient": None}],
        kinds=["choices", "captured"],
        selections=1,
    )
    verdict = classify(clean, asr, asr)
    assert verdict["norepair"] == "wrong_content"
    assert verdict["repair"] == "wrong_content"


def test_noop_lane_capture_is_unsafe_and_choices_are_nuisance() -> None:
    clean = _outcome(None, kinds=["noop"])  # corpus read-speech, no command
    hallucinated = _outcome([dict(REMINDER)], kinds=["captured"])
    nuisance = _outcome(None, kinds=["choices"])
    quiet = _outcome(None, kinds=["noop"])
    assert classify(clean, hallucinated, hallucinated)["norepair"] == "unsafe_capture"
    assert classify(clean, nuisance, nuisance)["norepair"] == "nuisance_choices"
    assert classify(clean, quiet, quiet)["norepair"] == "exact"


def test_refusal_lane_preserved_refusal_is_exact_and_capture_is_unsafe() -> None:
    clean = _outcome(None, kinds=["refused"])
    refused = _outcome(None, kinds=["refused"])
    captured = _outcome([dict(REMINDER)], kinds=["captured"])
    assert classify(clean, refused, refused)["lane"] == "refusal"
    assert classify(clean, refused, refused)["norepair"] == "exact"
    assert classify(clean, captured, captured)["norepair"] == "unsafe_capture"


def test_wer_and_jaccard_basics() -> None:
    assert wer("remind me to stretch", "remind me to stretch") == 0.0
    assert wer("remind me to stretch", "remind me too stretch") == pytest.approx(0.25)
    assert token_jaccard("water the tomato plants", "water tomato plants") == 1.0


# ---------------------------------------------------------- live routing

def test_route_lines_clean_reminder_is_captured() -> None:
    outcome = route_lines(["Remind me to water the tomato plants this evening"])
    assert outcome.effect == "captured"
    assert normalize_action(outcome.captured[0]["requested_action"]) == "reminder"


def test_direct_capture_and_repair_capture_vocabularies_are_bridged() -> None:
    # Direct captures store verbs ("remind"); repair selections store policy
    # taxonomy types ("reminder"). Scoring must treat them as equivalent.
    direct = {"requested_action": "remind", "subject": "water the plants", "recipient": None}
    via_repair = {"requested_action": "reminder", "subject": "water the plants", "recipient": None}
    assert intents_match(direct, via_repair)
    choice = {"action_type": "reminder", "label": "a reminder to water the plants", "position": 1}
    assert choice_matches(choice, direct)


def test_route_lines_cooperative_repair_selects_matching_choice() -> None:
    clean = route_lines(["Remind me to do my speech exercise this afternoon"])
    assert clean.effect == "captured"
    degraded = "speech exercise... this... you know..."
    norepair = route_lines([degraded])
    assert norepair.effect == "choices"  # degraded line alone must not capture
    with_repair = route_lines([degraded], targets=clean.captured)
    assert with_repair.repair_selections >= 1
    verdict = classify(clean, norepair, with_repair)
    assert verdict["lane"] == "intent"
    assert verdict["norepair"] == "safe_miss"
    assert verdict["repair"] == "repair_recovered"


def test_route_lines_alternates_recover_erased_recipient() -> None:
    # The motivating n-best case: ASR erased "Tell Sarah" into "There a";
    # a second model's hypothesis carries the recipient through repair.
    clean = route_lines(["Tell Sarah physio went well today"])
    degraded = "There a physio went well today."
    norepair = route_lines([degraded])
    with_nbest = route_lines(
        [degraded],
        targets=clean.captured,
        alternates=["Tell Sarah physio went well today"],
    )
    verdict = classify(clean, norepair, with_nbest)
    assert verdict["lane"] == "intent"
    assert verdict["repair"] in ("exact", "repair_recovered")
    assert with_nbest.captured[0]["recipient"] == "Sarah"


def test_route_lines_is_isolated_between_calls() -> None:
    route_lines(["Remind me to water the tomato plants this evening"])
    fresh = route_lines(["hello there"])
    assert fresh.captured == []


# ------------------------------------------------------------- ASR cache

def test_cached_asr_runs_live_once_then_reads_cache(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    calls = {"n": 0}

    def fake_transcriber(_clip):
        calls["n"] += 1
        return [" hello world "]

    asr = CachedASR(ASRConfig(model_size="tiny"), tmp_path, transcriber=fake_transcriber)
    first = asr.transcribe(clip)
    second = asr.transcribe(clip)
    assert calls["n"] == 1
    assert first.cached is False and second.cached is True
    assert second.text == "hello world"


def test_cache_key_distinguishes_configs(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    for config in (ASRConfig("tiny"), ASRConfig("small"), ASRConfig("tiny", initial_prompt="Sarah")):
        CachedASR(config, tmp_path, transcriber=lambda _c: ["x"]).transcribe(clip)
    assert len(list((tmp_path / "asr_cache").glob("*.json"))) == 3


# --------------------------------------------------- synthetic generator

def test_degradations_are_dysarthria_shaped() -> None:
    from benchmark.audio_harness.generate_synthetic import (
        degrade_clipped_start,
        degrade_ellipsis,
        degrade_faded_ending,
        degrade_verb_dropped,
    )

    cmd = "Tell Sarah the physio visit went well today"
    assert degrade_verb_dropped(cmd) == "Sarah the physio visit went well today"
    assert degrade_verb_dropped("Remind me to take my walk") == "to take my walk"
    assert "..." in degrade_ellipsis(cmd)
    assert degrade_clipped_start(cmd) == "the physio visit went well today"
    assert degrade_faded_ending(cmd) == "Tell Sarah the physio visit went"
    # short utterances are left alone rather than degraded to nothing
    assert degrade_clipped_start("Stop") == "Stop"
    assert degrade_faded_ending("Stop") == "Stop"
    # a faded negation stays a negation — a valuable hard negative
    assert degrade_faded_ending("No, don't send that yet") == "No, don't send"


def test_generator_manifest_schema_is_loadable(tmp_path: Path) -> None:
    from benchmark.audio_harness.generate_synthetic import COMMANDS, DATASET

    audio = tmp_path / "cmd00_clean.wav"
    audio.write_bytes(b"RIFF-fake")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset": DATASET,
                "clips": [
                    {
                        "sha256": "c" * 64,
                        "canonical_path": str(audio),
                        "dataset": DATASET,
                        "language": "en",
                        "speaker_condition": "synthetic",
                        "provenance": "synthetic",
                        "oracle_label": COMMANDS[0],
                        "duration_sec": None,
                    }
                ],
            }
        )
    )
    clips, excluded = load_clips(manifest)
    assert len(clips) == 1
    assert clips[0].oracle == COMMANDS[0]
    assert sum(excluded.values()) == 0


# ------------------------------------------------------------- manifest

def test_load_clips_excludes_unlabeled_and_unknown_provenance(tmp_path: Path) -> None:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF-fake")
    manifest = tmp_path / "manifest.json"
    entry = {
        "sha256": "b" * 64,
        "canonical_path": str(audio),
        "dataset": "test/corpus",
        "language": "en",
        "speaker_condition": "dysarthric",
        "provenance": "public",
        "oracle_label": "hello",
        "duration_sec": 1.0,
    }
    manifest.write_text(
        json.dumps(
            {
                "clips": [
                    entry,
                    {**entry, "oracle_label": None},
                    {**entry, "provenance": "unknown"},
                    {**entry, "canonical_path": str(tmp_path / "missing.wav")},
                ]
            }
        )
    )
    clips, excluded = load_clips(manifest)
    assert len(clips) == 1
    assert excluded == {
        "no_oracle_label": 1,
        "unknown_provenance": 1,
        "missing_file": 1,
        "private_excluded": 0,
    }
    assert clips[0].clip_id == f"corpus:{'b' * 12}"


def test_private_provenance_is_excluded_unless_explicitly_included(tmp_path: Path) -> None:
    # web-private (Hermes local validation scrape) and pilot-consented
    # (family recordings) never enter a default run — the "this is never
    # released" promise is enforced mechanically, not by memory.
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF-fake")
    manifest = tmp_path / "manifest.json"
    base = {
        "sha256": "d" * 64,
        "canonical_path": str(audio),
        "dataset": "web-scout/validation",
        "language": "en",
        "speaker_condition": "parkinsons",
        "oracle_label": "hello there",
        "duration_sec": 1.0,
    }
    manifest.write_text(
        json.dumps(
            {
                "clips": [
                    {**base, "provenance": "web-private"},
                    {**base, "sha256": "e" * 64, "provenance": "pilot-consented"},
                    {**base, "sha256": "f" * 64, "provenance": "public"},
                ]
            }
        )
    )
    clips, excluded = load_clips(manifest)
    assert [c.provenance for c in clips] == ["public"]
    assert excluded["private_excluded"] == 2

    clips, excluded = load_clips(manifest, include_private=True)
    assert len(clips) == 3
    assert excluded["private_excluded"] == 0


def test_reports_never_contain_filesystem_paths(tmp_path: Path) -> None:
    from benchmark.audio_harness.run import evaluate_model, summarize

    clip = _clip(tmp_path, oracle="Remind me to water the tomato plants this evening")
    # Run the real evaluate_model with an injected fake ASR via cache pre-seed.
    config = ASRConfig(model_size="tiny")
    CachedASR(config, tmp_path, transcriber=lambda _c: ["Remind me to water the tomato plants tonight"]).transcribe(clip)
    model_result = evaluate_model("tiny", [clip], tmp_path, beam_size=5, initial_prompt=None)
    payload = {"models": [summarize(model_result)], "per_clip": {"tiny": model_result["rows"]}}
    dumped = json.dumps(payload)
    assert str(tmp_path) not in dumped
    assert "/Users/" not in dumped
