"""The expression state machine unit-tests in Node, without WebGL.

The semantic expression state (app/parker/static/converse/expression.js)
is the durable contract of the Reachy Mini slice: real signals in, small
truthful state out, any renderer downstream. The JS spec
(tests/js/expression.spec.js) pins phase transitions, stale-event
rejection, energy hysteresis, overlay TTLs, and the never-claim-execution
rule. This wrapper runs it under pytest so `make test` and CI carry it.

Node is present on the dev Macs and the ubuntu CI runners; if it is
genuinely absent the suite says so instead of silently passing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
STATIC_DIR = TESTS_DIR.parent / "app" / "parker" / "static"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _run_node(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", *args], capture_output=True, text=True, timeout=60
    )


def test_expression_state_machine_spec_passes():
    result = _run_node(str(TESTS_DIR / "js" / "expression.spec.js"))
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    assert "FAIL" not in result.stdout


def test_expression_module_is_valid_classic_script():
    # The page loads expression.js as a classic script; a stray `export`
    # or syntax slip must fail here, not in the living room.
    result = _run_node("--check", str(STATIC_DIR / "converse" / "expression.js"))
    assert result.returncode == 0, result.stderr


def test_reachy_renderer_module_parses_as_esm():
    # reachy.js is an ES module importing the vendored Three.js; parse it
    # with the module goal (node --check uses the CommonJS goal for .js).
    source = (STATIC_DIR / "converse" / "reachy.js").read_text()
    result = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=source,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_converse_page_inline_scripts_parse(tmp_path):
    # The page's JavaScript lives inside a Python string; a stray escape
    # or syntax slip must fail here, not in the living room. (The module
    # boot script uses only dynamic import(), which parses in the classic
    # goal too.)
    import re

    from app.parker.converse_ui import CONVERSE_PAGE_HTML

    scripts = [
        s
        for s in re.findall(
            r"<script(?:\s[^>]*)?>(.*?)</script>", CONVERSE_PAGE_HTML, re.S
        )
        if s.strip()
    ]
    assert len(scripts) >= 2, "expected the conversation script and the scene boot"
    for index, source in enumerate(scripts):
        path = tmp_path / f"inline-{index}.js"
        path.write_text(source)
        result = _run_node("--check", str(path))
        assert result.returncode == 0, f"inline script {index}:\n{result.stderr}"
