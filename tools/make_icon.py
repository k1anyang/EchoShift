"""Draw the application icon.

Matches the mark in the window header: a rounded accent square with a white,
geometric E whose middle stroke becomes a right-facing shift arrow.  Written
as code so every Windows icon size comes from one reproducible source.

    python tools/make_icon.py [output.ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echoshift.gui.theme import DARK  # noqa: E402

SIZES = [
    (16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
    (48, 48), (64, 64), (128, 128), (256, 256),
]

#: Drawn on a 256x256 canvas, then scaled down by Pillow.
CANVAS = 256
RADIUS = 56


def _hex(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def draw() -> "object":
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Rounded accent tile, matching the in-app header mark.
    draw.rounded_rectangle(
        (0, 0, CANVAS - 1, CANVAS - 1), radius=RADIUS, fill=_hex(DARK.accent)
    )

    ink = _hex(DARK.accent_text)
    stroke = 22
    left, top, middle, bottom = 67, 66, 128, 190
    # The open right edge keeps the E legible; the middle arm carries motion.
    draw.rounded_rectangle((left, top, left + stroke, bottom), radius=8, fill=ink)
    draw.rounded_rectangle((left, top, 158, top + stroke), radius=8, fill=ink)
    draw.rounded_rectangle((left, bottom - stroke, 158, bottom), radius=8, fill=ink)
    draw.rounded_rectangle((left, middle - stroke // 2, 174, middle + stroke // 2), radius=8, fill=ink)
    draw.polygon([(160, 91), (216, middle), (160, 165)], fill=ink)

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
