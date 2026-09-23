#!/usr/bin/env python3
"""Pack the selected official Reachy Mini CAD shells for the browser.

Build-time only; requires the backend's existing NumPy. Download the six
STLs named below from the pinned upstream revision into the input directory.
No network calls, mesh simplification, or runtime Python dependency added.
Coordinates stay in CAD metres. Vertices are welded at 10 micrometres and
stored as signed 16-bit coordinates; triangle indices are unsigned 32-bit.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

REVISION = "234a978e4426895fc88d864e7f154643aea77f53"
PARTS = (
    "body_foot_3dprint", "body_down_3dprint", "body_top_3dprint",
    "head_back_3dprint", "head_front_3dprint", "head_mic_3dprint",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    packed = bytearray()
    manifest = {"revision": REVISION, "coordinate_scale": 0.00001, "meshes": {}}
    dtype = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attr", "<u2")])
    for name in PARTS:
        source = (args.input / f"{name}.stl").read_bytes()
        count = int.from_bytes(source[80:84], "little")
        assert len(source) == 84 + count * 50, f"not a binary STL: {name}"
        points = np.frombuffer(source, dtype=dtype, offset=84)["vertices"].reshape(-1, 3)
        quantized = np.rint(points / manifest["coordinate_scale"])
        assert np.isfinite(quantized).all() and abs(quantized).max() < 32768
        vertices, indices = np.unique(quantized.astype("<i2"), axis=0, return_inverse=True)
        position_offset = len(packed)
        packed.extend(vertices.astype("<i2").tobytes())
        packed.extend(bytes((-len(packed)) % 4))
        index_offset = len(packed)
        packed.extend(indices.astype("<u4").tobytes())
        manifest["meshes"][name] = {
            "position_offset": position_offset, "vertex_count": len(vertices),
            "index_offset": index_offset, "index_count": len(indices),
            "source_sha256": hashlib.sha256(source).hexdigest(),
        }
    manifest["sha256"] = hashlib.sha256(packed).hexdigest()
    (args.output / "reachy-mini.bin").write_bytes(packed)
    (args.output / "reachy-mini.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Packed {len(PARTS)} CAD meshes: {len(packed):,} bytes")


if __name__ == "__main__":
    main()
