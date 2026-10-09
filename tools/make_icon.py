"""Render the application icon files.

The mark itself is defined in :mod:`echoshift.brandmark` and shared with the
in-app header, so the window icon, the taskbar icon, the exe's resource and the
header cannot drift apart.  This script only supplies the raster side: it draws
the tile and the mark at 256x256 and lets Pillow produce every Windows size.

    python tools/make_icon.py [output.ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echoshift.brandmark import (  # noqa: E402
    MARK_ARROW,
    MARK_LINES,
    MARK_RADIUS,
    MARK_STROKE,
)
from echoshift.gui.theme import DARK  # noqa: E402

SIZES = [
    (16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
    (48, 48), (64, 64), (128, 128), (256, 256),
]

#: Drawn on a 256x256 canvas, then scaled down by Pillow.
CANVAS = 256


def _hex(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def draw() -> "object":
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Rounded accent tile, matching the in-app header mark.
    draw.rounded_rectangle(
        (0, 0, CANVAS - 1, CANVAS - 1),
        radius=MARK_RADIUS * CANVAS,
        fill=_hex(DARK.accent),
    )

    ink = _hex(DARK.accent_text)
    stroke = MARK_STROKE * CANVAS
    radius = stroke / 2

    # Stroked segments are drawn as capsules so they match the canvas renderer,
    # which uses round caps via create_line.
    for x1, y1, x2, y2 in MARK_LINES:
        ax, ay, bx, by = (v * CANVAS for v in (x1, y1, x2, y2))
        draw.line((ax, ay, bx, by), fill=ink, width=round(stroke))
        for cx, cy in ((ax, ay), (bx, by)):
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=ink)

    draw.polygon([(x * CANVAS, y * CANVAS) for x, y in MARK_ARROW], fill=ink)

    return image


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("需要 Pillow：python -m pip install pillow")
        return 1

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets" / "echoshift.ico"
    target.parent.mkdir(parents=True, exist_ok=True)

    image = draw()
    image.save(target, format="ICO", sizes=SIZES)
    print(f"图标已写入 {target}（{', '.join(f'{w}x{h}' for w, h in SIZES)}）")

    preview = target.with_suffix(".png")
    image.resize((256, 256), Image.LANCZOS).save(preview)
    print(f"预览图 {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
