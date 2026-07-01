"""Audio-in eval harness: real audio -> local ASR -> TextSession -> scored routes.

Runs real (public-corpus and synthetic) audio clips through the actual
local transcriber and the actual ``TextSession`` routing, then scores the
ASR path against a clean path built from each clip's oracle transcript.
The headline metric is intent recovery on dysarthric speech, with and
without the repair-choice protocol.

Privacy and data policy: audio lives under the Operations artifacts
directory (``PARKER_AUDIO_ARTIFACTS_DIR``) and is only ever read. The
repo stores harness code and aggregate reports; reports identify clips by
dataset + content hash, never by filesystem path.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
