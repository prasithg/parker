"""Local-only audio transcription for the voice demo seam.

Transcribes an audio file with faster-whisper (CTranslate2 Whisper)
running entirely on this machine — no cloud speech APIs. The dependency
is optional and lazily imported: install it with ``make voice-deps``;
the core test suite injects a fake transcriber instead.

Privacy: this module only *reads* the input file. It never copies,
moves, or re-encodes audio, and nothing audio-derived is persisted
except the transcript text that flows into the normal capture pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

# A transcriber maps an audio file path to raw utterance lines.
Transcriber = Callable[[Path], list[str]]

# Sentence boundary within one Whisper segment: split after ./!/? followed
# by whitespace — but never after an ellipsis, which is effortful-speech
# disfluency the text loop routes to repair choices and must stay intact.
_SENTENCE_BOUNDARY = re.compile(r"(?<!\.\.)(?<=[.!?])\s+")

# Pause-free speech makes Whisper join two commands — either with a comma
# ("…this evening, tell Sarah…") or with "and" alone ("…stretch and tell
# Sarah…"). Split before the text loop's capture verbs in both forms.
# "and" without a capture verb (e.g. "apples and oranges") does not match.
_COMMAND_BOUNDARY = re.compile(
    r",\s+(?:and\s+)?(?=(?:tell|remind|message|send)\b)"  # comma form: ", [and] tell"
    r"|\s+and\s+(?=(?:tell|remind|message|send)\b)",       # bare form: " and tell"
    re.IGNORECASE,
)

VOICE_DEPS_HINT = (
    "faster-whisper is not installed. It is an optional, local-only "
    "dependency: run 'make voice-deps' to install it (the model runs on "
    "this machine; the first run downloads model weights to the local "
    "Hugging Face cache, after which transcription is fully offline)."
)


def lexicon_initial_prompt() -> str | None:
    """Whisper bias prompt from family contacts + the personal lexicon.

    ``PARKER_FAMILY_CONTACTS`` (the message-capability allowlist) and
    ``PERSONAL_LEXICON`` (everyday words the speaker actually uses — places,
    routines) are merged in ``app.parker.contacts.asr_bias_words``: enabling
    a contact automatically primes recognition for that name. Whisper treats
    the initial prompt as preceding context, nudging recognition toward
    these words — the cheapest rung of the per-user adaptation ladder.
    """

    from app.parker.contacts import asr_bias_words

    words = asr_bias_words()
    if not words:
        return None
    return "Words and names likely in this speech: " + ", ".join(words) + "."


# Default chosen by the real-audio eval (benchmark/reports/audio_real_eval_*):
# recovery plateaus at "base" (72.7% -> 90.9% with repair, 0 unsafe) while
# small/medium cost 3-8x the runtime for no recovery gain.
DEFAULT_ASR_MODEL = "base"


def load_local_transcriber(
    model_size: str = DEFAULT_ASR_MODEL,
    language: str | None = "en",
    initial_prompt: str | None = None,
) -> Transcriber:
    """Build a transcriber backed by a local faster-whisper model.

    ``initial_prompt`` defaults to the personal-lexicon bias prompt when the
    family has configured one.
    """

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(VOICE_DEPS_HINT) from exc

    from app import paths

    # PARKER_HOME/models when the app downloaded the weights; an existing
    # HF-cache copy keeps being used as-is (None = faster-whisper default).
    download_root = paths.whisper_download_root(model_size)
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=str(download_root) if download_root is not None else None,
    )
    prompt = initial_prompt if initial_prompt is not None else lexicon_initial_prompt()

    def _transcribe(audio_path: Path) -> list[str]:
        segments, _info = model.transcribe(str(audio_path), language=language, initial_prompt=prompt)
        return [segment.text for segment in segments]

    return _transcribe


def split_utterances(lines: list[str]) -> list[str]:
    """Split raw transcript lines into one utterance per command/sentence.

    Whisper merges back-to-back commands spoken without a pause into a
    single segment; ``TextSession.handle`` routes one utterance at a
    time, so boundaries are restored here. Ellipsis disfluencies are
    preserved verbatim — they are signal, not noise.
    """

    utterances: list[str] = []
    for line in lines:
        for sentence in _SENTENCE_BOUNDARY.split(line):
            utterances.extend(_COMMAND_BOUNDARY.split(sentence))
    return [clean for clean in (part.strip(" ,") for part in utterances) if clean]


def transcribe_audio(
    audio_path: str | Path,
    *,
    transcriber: Transcriber | None = None,
    model_size: str = DEFAULT_ASR_MODEL,
) -> list[str]:
    """Transcribe a local audio file into one utterance line per command.

    Raw segment lines from the transcriber are split on sentence and
    command boundaries so each returned line is one utterance for
    ``TextSession.handle``.
    """

    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"audio file not found: {path}")
    transcribe = transcriber or load_local_transcriber(model_size=model_size)
    return split_utterances(transcribe(path))
