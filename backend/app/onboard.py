"""``parker onboard`` — terminal fallback for the desktop onboarding wizard.

Same questions, same config.json writer, no windows: patient name,
family contacts, lexicon extras, TTS voice, and the consent flags in
plain language. Repair-event capture is opt-IN (default no), matching
the engine default.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from app import paths
from app.parker.family_config import write_family_config
from app.voice.transcribe import DEFAULT_ASR_MODEL


def _ask(prompt: str, default: str, input_fn: Callable[[str], str]) -> str:
    shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
    answer = input_fn(shown).strip()
    return answer or default


def _ask_yes_no(prompt: str, default: bool, input_fn: Callable[[str], str]) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input_fn(f"{prompt} ({hint}): ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def run_terminal_onboarding(
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> int:
    print_fn("Parker setup — a few questions from the family administrator.")
    print_fn(f"Settings are stored in {paths.config_path()} (never secrets).\n")

    answers: dict[str, Any] = {}
    answers["patient_name"] = _ask("Who is Parker for? First name", "Dad", input_fn)
    answers["parker_family_contacts"] = _ask(
        "Family contacts Parker may send messages to after a spoken yes "
        "(comma-separated names; leave empty to keep every message "
        "caregiver-approved)",
        "",
        input_fn,
    )
    answers["personal_lexicon"] = _ask(
        "Extra words Parker should be primed to hear (places, routines; comma-separated)",
        "",
        input_fn,
    )
    answers["parker_tts_voice"] = _ask(
        "Voice for spoken replies (a macOS `say` voice name; empty = system default)",
        "",
        input_fn,
    )
    answers["repair_event_capture_consented"] = _ask_yes_no(
        "May Parker keep a local, text-only record of repair exchanges to "
        "improve understanding? Nothing leaves this machine; no audio is "
        "stored. Opt-in",
        False,
        input_fn,
    )
    answers["onboarding_completed"] = True

    written = write_family_config(answers)
    print_fn(f"\nSaved {len(written)} settings to {paths.config_path()}")

    if paths.whisper_model_location(DEFAULT_ASR_MODEL) == "missing":
        print_fn(
            f"\nThe speech model ({DEFAULT_ASR_MODEL}) is not downloaded yet — "
            "run `parker download-model` before the first conversation."
        )
    print_fn("Parker is set up. Run `parker doctor` to verify this machine.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_terminal_onboarding())
