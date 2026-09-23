"""The Converse presence assets: same-origin, pinned, traversal-safe.

The patient surface never fetches runtime code from a CDN — Three.js is
vendored (MIT, version + hash pinned here) and served by the engine at
/parker/converse/static/*, from the repo and the PyInstaller sidecar
alike (parker.spec ships the directory as package data).
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.parker.converse_router import converse_static

client = TestClient(app)

STATIC_DIR = Path(__file__).parent.parent / "app" / "parker" / "static"

THREE_SHA256 = {
    "three.module.min.js": "86bcee248b64f44bcfc23c331ae74619061957d59cab040171dcb6fb5900beb6",
    # The 0.185 module build imports its core build at runtime — both ship.
    "three.core.min.js": "05b2609338c76cd65daf74f3ac515bc9a5045e1b3b33edc07d8c9bd55250fa90",
}


def test_presence_assets_are_served_same_origin():
    for asset, marker in (
        ("converse/expression.js", "ParkerExpression"),
        ("converse/reachy.js", "three.module.min.js"),
        ("converse/reachy-model.js", "reachy-mini.bin"),
        ("vendor/three/three.module.min.js", "three.core.min.js"),
        ("vendor/three/three.core.min.js", "revision"),
    ):
        response = client.get(f"/parker/converse/static/{asset}")
        assert response.status_code == 200, asset
        assert response.headers["content-type"].startswith("text/javascript")
        assert marker in response.text


def test_vendored_three_is_the_pinned_build():
    for name, expected in THREE_SHA256.items():
        payload = (STATIC_DIR / "vendor" / "three" / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected, (
            f"{name} changed — update the pin in the vendor README "
            "and here together, deliberately"
        )


def test_vendored_three_ships_its_mit_license():
    license_text = (STATIC_DIR / "vendor" / "three" / "LICENSE").read_text()
    assert "MIT" in license_text
    readme = (STATIC_DIR / "vendor" / "three" / "README.md").read_text()
    assert "0.185.1" in readme
    for digest in THREE_SHA256.values():
        assert digest in readme


def test_reachy_cad_assets_are_complete_and_valid():
    """A truncated packaged mesh must fail here, before the page falls back."""
    root = STATIC_DIR / "vendor" / "reachy-mini"
    manifest = json.loads((root / "reachy-mini.json").read_text())
    payload = (root / "reachy-mini.bin").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == manifest["sha256"]
    assert set(manifest["meshes"]) == {
        "body_foot_3dprint", "body_down_3dprint", "body_top_3dprint",
        "head_back_3dprint", "head_front_3dprint", "head_mic_3dprint",
    }
    for mesh in manifest["meshes"].values():
        assert len(mesh["source_sha256"]) == 64
        assert mesh["position_offset"] % 2 == mesh["index_offset"] % 4 == 0
        assert mesh["position_offset"] + mesh["vertex_count"] * 6 <= mesh["index_offset"]
        end = mesh["index_offset"] + mesh["index_count"] * 4
        assert end <= len(payload) and mesh["index_count"] % 3 == 0
        indices = struct.unpack_from(f'<{mesh["index_count"]}I', payload, mesh["index_offset"])
        assert max(indices) < mesh["vertex_count"]
    assert "Apache License" in (root / "LICENSE").read_text()
    for name in ("reachy-mini.bin", "reachy-mini.json"):
        response = client.get(f"/parker/converse/static/vendor/reachy-mini/{name}")
        assert response.status_code == 200
        assert response.content == (root / name).read_bytes()


def test_static_route_never_escapes_its_root():
    for attempt in (
        "../converse_ui.py",
        "../../config.py",
        "converse/../../realtime.py",
        "/etc/hosts",
    ):
        with pytest.raises(HTTPException) as excinfo:
            converse_static(attempt)
        assert excinfo.value.status_code == 404, attempt


def test_unknown_asset_is_a_404():
    response = client.get("/parker/converse/static/converse/nope.js")
    assert response.status_code == 404


def test_page_references_only_assets_that_exist():
    # Every /parker/converse/static/ path the page mentions must resolve —
    # a renamed file must fail here, not as a silent 404 in the living room.
    html = client.get("/parker/converse").text
    import re

    for path in re.findall(r"/parker/converse/static/([\w./-]+)", html):
        assert (STATIC_DIR / path).is_file(), path
