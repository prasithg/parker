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


def test_reachy_model_module_parses_as_esm():
    source = (STATIC_DIR / "converse" / "reachy-model.js").read_text()
    result = subprocess.run(
        ["node", "--input-type=module", "--check"], input=source,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def _extract_inline_scripts(html: str) -> list[str]:
    """Every non-empty inline script of a page, in document order: the
    conversation runtime first, then the scene boot module."""

    import re

    scripts = [
        s
        for s in re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)
        if s.strip()
    ]
    assert scripts, "no inline scripts found in the page"
    return scripts


def _extract_page_script(html: str) -> str:
    """The first inline script (the conversation runtime) from a page."""

    return _extract_inline_scripts(html)[0]


def test_lab_page_lifecycle_spec_passes(tmp_path):
    """The REAL lab page script under Node: boot, capture teardown on page
    hide, Stop terminality + receipt flushing."""

    from app.parker.converse_ui import CONVERSE_PAGE_HTML

    page_script = tmp_path / "lab-page.js"
    page_script.write_text(_extract_page_script(CONVERSE_PAGE_HTML))
    result = _run_node(
        str(TESTS_DIR / "js" / "converse_page.spec.js"), str(page_script)
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    assert "FAIL" not in result.stdout


def test_companion_page_lifecycle_spec_passes(tmp_path):
    """The REAL companion page script under Node — real power semantics,
    persisted settings, CC, spoken-confirmation cards, and every
    interleaving the independent review proved broken on the live lane
    (guard TTS vs off/close, drain-vs-response truth, stale opens,
    page-hide teardown, transition receipts). The scene boot (the second
    inline script) rides along so the page-to-scene reduced-motion seam
    and the scene receipt's wait for the session are pinned too."""

    from app.parker.companion_ui import COMPANION_PAGE_HTML

    scripts = _extract_inline_scripts(COMPANION_PAGE_HTML)
    page_script = tmp_path / "companion-page.js"
    page_script.write_text(scripts[0])
    scene_script = tmp_path / "companion-scene.js"
    scene_script.write_text(scripts[1])
    result = _run_node(
        str(TESTS_DIR / "js" / "companion_page.spec.js"),
        str(page_script),
        str(scene_script),
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    assert "FAIL" not in result.stdout


def test_converse_page_inline_scripts_parse(tmp_path):
    # Both pages' JavaScript lives inside Python strings; a stray escape
    # or syntax slip must fail here, not in the living room. (The module
    # boot scripts use only dynamic import(), which parses in the classic
    # goal too.)
    from app.parker.companion_ui import COMPANION_PAGE_HTML
    from app.parker.converse_ui import CONVERSE_PAGE_HTML

    for page_name, html in (("lab", CONVERSE_PAGE_HTML), ("companion", COMPANION_PAGE_HTML)):
        scripts = _extract_inline_scripts(html)
        assert len(scripts) >= 2, f"{page_name}: expected the runtime script and the scene boot"
        for index, source in enumerate(scripts):
            path = tmp_path / f"{page_name}-inline-{index}.js"
            path.write_text(source)
            result = _run_node("--check", str(path))
            assert result.returncode == 0, f"{page_name} inline script {index}:\n{result.stderr}"
