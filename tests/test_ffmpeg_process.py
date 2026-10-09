"""Subprocess lifecycle regressions for the ffmpeg driver."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from echoshift.core.ffmpeg import run_with_progress
from echoshift.errors import CancelledError, EchoShiftError


def _silent_sleep(seconds: float) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds!r})"]


def test_silent_process_can_be_cancelled_without_waiting_for_stdout() -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.10, cancel.set)
    timer.start()
    started = time.perf_counter()
    try:
        with pytest.raises(CancelledError):
            run_with_progress(
                _silent_sleep(1.5),
                total_duration=None,
                cancel=cancel,
                timeout=5,
            )
    finally:
        timer.cancel()

    assert time.perf_counter() - started < 0.8


def test_silent_process_is_terminated_after_progress_timeout() -> None:
    started = time.perf_counter()
    with pytest.raises(EchoShiftError, match="无进度"):
        run_with_progress(
            _silent_sleep(1.5),
            total_duration=None,
            timeout=0.10,
        )

    assert time.perf_counter() - started < 0.8
