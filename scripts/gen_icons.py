#!/usr/bin/env python3
"""Generate Parker.app icons — stdlib only, deterministic.

Writes:
- desktop/src-tauri/icons/source-icon.png  (1024², indigo tile + speech bubble;
  feed to `cargo tauri icon` to produce the full icon set)
- desktop/src-tauri/icons/tray-{idle,listening,speaking}.png (44², macOS
  template style: black shapes on alpha, the system recolors them)

A speech bubble is the whole product in one glyph: Parker is the thing
in the room you talk to.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "desktop" / "src-tauri" / "icons"

INDIGO = (59, 91, 219, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)
BLACK = (0, 0, 0, 255)


def write_png(path: Path, size: int, pixel_fn) -> None:
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter: none
        for x in range(size):
            rows.extend(pixel_fn(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _inside_rounded_rect(x: float, y: float, size: float, radius: float) -> bool:
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def _bubble_alpha(x: float, y: float, size: float, filled: bool) -> float:
    """Speech bubble: circle + lower-left tail. Returns coverage 0..1."""

    cx, cy, r = size * 0.5, size * 0.46, size * 0.30
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    ring = size * 0.055
    if filled:
        body = 1.0 if d <= r else max(0.0, 1.0 - (d - r))
    else:
        body = 1.0 if abs(d - r) <= ring / 2 else max(0.0, 1.0 - (abs(d - r) - ring / 2))
    # Tail: triangle from bubble toward bottom-left.
    tx0, ty0 = size * 0.34, size * 0.62
    tx1, ty1 = size * 0.24, size * 0.88
    tx2, ty2 = size * 0.52, size * 0.72

    def sign(ax, ay, bx, by, px, py):
        return (px - bx) * (ay - by) - (ax - bx) * (py - by)

    b1 = sign(tx0, ty0, tx1, ty1, x, y) < 0
    b2 = sign(tx1, ty1, tx2, ty2, x, y) < 0
    b3 = sign(tx2, ty2, tx0, ty0, x, y) < 0
    tail = 1.0 if (b1 == b2 == b3) else 0.0
    return max(body, tail)


def app_icon(x: int, y: int) -> bytes:
    size = 1024
    # macOS-style margins: the tile occupies the middle ~82%.
    margin = size * 0.09
    if not _inside_rounded_rect(x - margin, y - margin, size - 2 * margin, (size - 2 * margin) * 0.225):
        return bytes(CLEAR)
    if x < margin or y < margin or x > size - margin or y > size - margin:
        return bytes(CLEAR)
    coverage = _bubble_alpha(x, y, size, filled=True)
    if coverage > 0:
        r, g, b, a = WHITE
        return bytes(
            (
                int(INDIGO[0] + (r - INDIGO[0]) * coverage),
                int(INDIGO[1] + (g - INDIGO[1]) * coverage),
                int(INDIGO[2] + (b - INDIGO[2]) * coverage),
                255,
            )
        )
    return bytes(INDIGO)


def tray_icon(filled: bool, waves: bool):
    def render(x: int, y: int) -> bytes:
        size = 44
        coverage = _bubble_alpha(x, y, size, filled=filled)
        if waves:
            # Two short arcs to the right of the bubble — "speaking".
            cx, cy = size * 0.5, size * 0.46
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            angle_ok = x > cx + size * 0.28 and abs(y - cy) < size * 0.30
            for radius in (size * 0.40, size * 0.50):
                if angle_ok and abs(d - radius) <= size * 0.045:
                    coverage = 1.0
        if coverage <= 0:
            return bytes(CLEAR)
        return bytes((0, 0, 0, int(255 * min(1.0, coverage))))

    return render


def main() -> int:
    write_png(OUT_DIR / "source-icon.png", 1024, app_icon)
    write_png(OUT_DIR / "tray-idle.png", 44, tray_icon(filled=False, waves=False))
    write_png(OUT_DIR / "tray-listening.png", 44, tray_icon(filled=True, waves=False))
    write_png(OUT_DIR / "tray-speaking.png", 44, tray_icon(filled=True, waves=True))
    print(f"icons written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
