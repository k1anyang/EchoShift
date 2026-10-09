"""The brand mark is described once and consumed by both renderers.

The window header paints it on a Tk ``Canvas``; the ``.ico`` shipped with the
app and baked into the exe is rendered with Pillow. They cannot share drawing
code, so they share
:mod:`echoshift.brandmark` -- and these tests exist because the two used to be
maintained separately, which is how a logo drifts.
"""

from __future__ import annotations

import pytest

from echoshift.brandmark import (
    MARK_ARROW,
    MARK_LINES,
    MARK_RADIUS,
    MARK_STROKE,
    mark_arrow,
    mark_bounds,
    mark_strokes,
)


def test_the_geometry_is_expressed_in_tile_relative_units():
    for value in (MARK_RADIUS, MARK_STROKE):
        assert 0 < value < 0.5, value
    for x1, y1, x2, y2 in MARK_LINES:
        for x, y in ((x1, y1), (x2, y2)):
            assert 0 <= x <= 1 and 0 <= y <= 1, (x, y)
    for x, y in MARK_ARROW:
        assert 0 <= x <= 1 and 0 <= y <= 1, (x, y)


def test_the_mark_sits_in_the_middle_of_its_tile():
    """An off-centre mark looks broken at every size, so pin the bounding box."""
    x1, y1, x2, y2 = mark_bounds()
    assert (x1 + x2) / 2 == pytest.approx(0.5, abs=1e-9)
    assert (y1 + y2) / 2 == pytest.approx(0.5, abs=1e-9)
    assert x2 <= 1.0 and y2 <= 1.0
    # Leaves a usable margin, or the mark would touch the tile's corners.
    assert x1 > 0.1 and y1 > 0.1


def test_the_arrow_continues_the_middle_arm():
    """The middle arm has to reach the arrow, or the E reads as two shapes."""
    middle = next(
        stroke
        for stroke in mark_strokes()
        if stroke.y1 == stroke.y2 == pytest.approx(0.5)
    )
    base_top, _tip, base_bottom = MARK_ARROW
    assert middle.x2 <= base_top[0], "中横与箭头之间有缝"
    # The arm meets the arrow's vertical centre.
    assert middle.y1 == pytest.approx((base_top[1] + base_bottom[1]) / 2)
    # ...and the tip is the right-most point, so the motion direction is clear.
    assert MARK_ARROW[1][0] == max(x for x, _y in MARK_ARROW)


def test_the_shapes_stay_within_the_tile_at_any_scale():
    for scale in (16, 32, 48, 256):
        x1, y1, x2, y2 = (value * scale for value in mark_bounds())
        assert 0 <= x1 and 0 <= y1 and x2 <= scale and y2 <= scale


@pytest.mark.parametrize("shorten", [0.0, 0.5, 1.0])
def test_strokes_can_be_shortened_without_leaving_their_start(shorten: float):
    for original, shortened in zip(MARK_LINES, mark_strokes(end=shorten)):
        assert (shortened.x1, shortened.y1) == (original[0], original[1])
        assert shortened.x2 == pytest.approx(
            original[0] + (original[2] - original[0]) * shorten
        )


def test_the_icon_files_exist_at_every_windows_size():
    from pathlib import Path

    from PIL import Image

    assets = Path(__file__).resolve().parents[1] / "assets"
    ico = assets / "echoshift.ico"
    png = assets / "echoshift.png"
    assert ico.is_file(), "缺少 assets/echoshift.ico（运行 tools/make_icon.py 生成）"
    assert png.is_file(), "缺少 assets/echoshift.png"

    with Image.open(ico) as image:
        sizes = set(image.info.get("sizes") or [])
    # Windows picks from these; a missing 16 or 32 shows a blurry taskbar icon.
    for required in ((16, 16), (32, 32), (48, 48), (256, 256)):
        assert required in sizes, f"ico 缺少 {required[0]}x{required[1]}：{sorted(sizes)}"


def test_the_png_preview_matches_the_ico():
    """Both files come from one generator, so they must not disagree."""
    from pathlib import Path

    from PIL import Image

    assets = Path(__file__).resolve().parents[1] / "assets"
    with Image.open(assets / "echoshift.ico") as ico:
        ico.size = (256, 256)
        large = ico.convert("RGBA").tobytes()
    with Image.open(assets / "echoshift.png") as png:
        preview = png.convert("RGBA").tobytes()
    assert large == preview, "echoshift.png 与 ico 的 256 尺寸不一致，重新生成图标"


def test_the_tk_renderer_uses_the_shared_geometry(tk_root):
    """If icons.py went back to hard-coded numbers, the two would drift again."""
    import tkinter as tk

    from echoshift.gui.icons import draw_icon

    canvas = tk.Canvas(tk_root, width=80, height=80)
    draw_icon(canvas, "brand", 40, 40, 64, "#ffffff", width=2)

    expected: set[tuple[int, int]] = set()
    for stroke in mark_strokes():
        for x, y in ((stroke.x1, stroke.y1), (stroke.x2, stroke.y2)):
            expected.add((round(8 + x * 64), round(8 + y * 64)))
    for x, y in mark_arrow():
        expected.add((round(8 + x * 64), round(8 + y * 64)))

    # Every coordinate the renderer emitted has to be one the geometry defines.
    drawn: set[tuple[int, int]] = set()
    for item in canvas.find_all():
        coords = canvas.coords(item)
        assert coords, "空图元"
        for index in range(0, len(coords), 2):
            drawn.add((round(coords[index]), round(coords[index + 1])))
    assert drawn, "brand 图标什么都没画"
    assert drawn <= expected, f"渲染器画了几何里没有的点：{sorted(drawn - expected)}"
    canvas.destroy()
