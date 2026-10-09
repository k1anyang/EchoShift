"""The hand-drawn CheckBox.

ttk's clam theme renders a *checked* checkbutton as a white cross, which reads
as "off" to anyone used to Windows.  These pin the replacement: a tick when on,
an empty box when off, and a `ttk.Checkbutton` never reappearing.
"""

from __future__ import annotations

import tkinter as tk

import pytest
from tkinter import ttk

from echoshift.gui.theme import DARK, build_fonts
from echoshift.gui.widgets import CheckBox


@pytest.fixture
def variable(tk_root):
    return tk.BooleanVar(master=tk_root, value=False)


def _checkbox(tk_root, variable, **kwargs) -> CheckBox:
    return CheckBox(
        tk_root, kwargs.pop("text", "转换后校验"), variable,
        palette=DARK, fonts=build_fonts(), **kwargs,
    )


def test_a_fresh_checkbox_reflects_its_variable(tk_root, variable):
    box = _checkbox(tk_root, variable)
    assert box.checked is False
    variable.set(True)
    assert box.checked is True
    box.destroy()


def test_clicking_toggles_the_variable(tk_root, variable):
    box = _checkbox(tk_root, variable)
    box.toggle()
    assert variable.get() is True
    box.toggle()
    assert variable.get() is False
    box.destroy()


def test_the_command_fires_on_toggle(tk_root, variable):
    calls: list[bool] = []
    box = _checkbox(tk_root, variable, command=lambda: calls.append(variable.get()))
    box.toggle()
    assert calls == [True]
    box.destroy()


def test_external_variable_changes_repaint_the_box(tk_root, variable):
    """The app sets these variables from saved config, not by clicking."""
    box = _checkbox(tk_root, variable)
    unchecked_items = len(box.find_all())
    variable.set(True)
    tk_root.update_idletasks()
    checked_items = len(box.find_all())
    # The tick is an extra canvas item on top of box + label.
    assert checked_items > unchecked_items
    box.destroy()


def test_a_checked_box_draws_a_box_a_tick_and_a_label(tk_root, variable):
    variable.set(True)
    box = _checkbox(tk_root, variable, text="保留标签")
    assert len(box.find_all()) == 3, "应有：底框、对勾、文字"
    box.destroy()


def test_an_unchecked_box_draws_no_tick(tk_root, variable):
    box = _checkbox(tk_root, variable, text="保留标签")
    assert len(box.find_all()) == 2, "未选中时不应有对勾"
    box.destroy()


def test_a_disabled_box_ignores_clicks(tk_root, variable):
    box = _checkbox(tk_root, variable)
    box.set_enabled(False)
    box.toggle()
    assert variable.get() is False
    box.destroy()


def test_the_box_is_wide_enough_for_its_label(tk_root, variable):
    narrow = _checkbox(tk_root, variable, text="保留")
    wide = _checkbox(tk_root, variable, text="完整解码校验")
    assert int(wide.cget("width")) > int(narrow.cget("width"))
    narrow.destroy()
    wide.destroy()


def test_clicking_the_canvas_toggles(tk_root, variable):
    import types

    box = _checkbox(tk_root, variable)
    box._on_click(types.SimpleNamespace(x=5, y=5))
    assert variable.get() is True
    box.destroy()


# --------------------------------------------------------------------------- #
# regression: clam's ambiguous cross must not come back
# --------------------------------------------------------------------------- #


def _widgets(widget: tk.Misc, kinds: tuple[type, ...]) -> list[tk.Misc]:
    found = [widget] if isinstance(widget, kinds) else []
    for child in widget.winfo_children():
        found.extend(_widgets(child, kinds))
    return found


def test_the_app_uses_no_ttk_checkbuttons(tk_root):
    from echoshift.gui.app import EchoShiftApp

    window = tk.Toplevel(tk_root)
    app = EchoShiftApp(window)
    tk_root.update()

    offenders = _widgets(window, (ttk.Checkbutton,))
    assert offenders == [], f"ttk.Checkbutton 会画出有歧义的叉号：{offenders}"
    assert len(_widgets(window, (CheckBox,))) >= 5, "选项区应有 5 个手绘复选框"

    app._closing = True
    window.destroy()


def test_nothing_asks_for_the_removed_checkbutton_style(tk_root):
    """A leftover style name would mean the swap was done half way."""
    from echoshift.gui.app import EchoShiftApp

    window = tk.Toplevel(tk_root)
    app = EchoShiftApp(window)
    tk_root.update()

    for widget in _widgets(window, (ttk.Checkbutton,)):
        assert "TCheckbutton" not in str(widget.cget("style")), widget

    from echoshift.gui.widgets import SegmentedControl

    segmented = _widgets(window, (SegmentedControl,))
    assert app.output_mode_control in segmented

    app._closing = True
    window.destroy()


def test_number_stepper_refreshes_boundary_button_states(tk_root):
    from echoshift.gui.theme import DARK, build_fonts
    from echoshift.gui.widgets import NumberStepper

    value = tk.IntVar(master=tk_root, value=1)
    stepper = NumberStepper(
        tk_root,
        value,
        minimum=1,
        maximum=3,
        palette=DARK,
        fonts=build_fonts(),
    )

    assert not stepper.minus._enabled
    assert stepper.plus._enabled
    value.set(3)
    assert stepper.minus._enabled
    assert not stepper.plus._enabled
    stepper.destroy()
