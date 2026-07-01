"""Load the consolidated Operations audio manifest into harness clips.

The manifest is produced in the Operations workspace (outside this repo)
and lists every unique audio clip with provenance, oracle transcript, and
speaker condition. Clips without an oracle transcript or with unknown
provenance are excluded from scoring — exclusions are counted, never
silent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ARTIFACTS_ENV = "PARKER_AUDIO_ARTIFACTS_DIR"
MANIFEST_FILENAME = "consolidated_audio_manifest.json"

ARTIFACTS_HINT = (
    f"{ARTIFACTS_ENV} is not set. Point it at the Operations audio artifacts "
    "directory containing consolidated_audio_manifest.json (audio never "
    "lives in this repo)."
)


@dataclass(frozen=True)
class Clip:
    sha256: str
    path: Path
    dataset: str
    language: str
    speaker_condition: str
    provenance: str
    oracle: str
    duration_sec: float | None

    @property
    def clip_id(self) -> str:
        """Public-safe identifier: dataset short name + content hash prefix."""
        return f"{self.dataset.rsplit('/', 1)[-1]}:{self.sha256[:12]}"


def artifacts_dir() -> Path:
    value = os.environ.get(ARTIFACTS_ENV)
    if not value:
        raise RuntimeError(ARTIFACTS_HINT)
    return Path(value)


def load_clips(manifest_path: Path | None = None) -> tuple[list[Clip], dict[str, int]]:
    """Return (usable clips, exclusion counts by reason)."""

    path = manifest_path or artifacts_dir() / MANIFEST_FILENAME
    payload = json.loads(path.read_text())
    clips: list[Clip] = []
    excluded = {"no_oracle_label": 0, "unknown_provenance": 0, "missing_file": 0}
    for entry in payload["clips"]:
        if not entry.get("oracle_label"):
            excluded["no_oracle_label"] += 1
            continue
        if entry.get("provenance") not in ("public", "synthetic"):
            excluded["unknown_provenance"] += 1
            continue
        clip_path = Path(entry["canonical_path"])
        if not clip_path.is_file():
            excluded["missing_file"] += 1
            continue
        clips.append(
            Clip(
                sha256=entry["sha256"],
                path=clip_path,
                dataset=entry["dataset"],
                language=entry.get("language") or "en",
                speaker_condition=entry.get("speaker_condition") or "other",
                provenance=entry["provenance"],
                oracle=entry["oracle_label"],
                duration_sec=entry.get("duration_sec"),
            )
        )
    return clips, excluded
