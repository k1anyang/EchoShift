"""Locating and driving the bundled ffmpeg / ffprobe toolchain."""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..errors import EchoShiftError, CancelledError, ProbeError, ToolNotFoundError
from ..paths import bundle_roots

__all__ = [
    "Toolchain",
    "find_toolchain",
    "bundled_dirs",
    "run",
    "run_with_progress",
    "ProcessLimiter",
    "terminate_active_processes",
    "CREATE_NO_WINDOW",
]

#: Suppresses the console window that would otherwise flash up for every
#: ffmpeg invocation when the GUI is running under pythonw.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_FFMPEG_EXE = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
_FFPROBE_EXE = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"

_ENV_OVERRIDE = "ECHOSHIFT_FFMPEG_DIR"
#: Names used before the rename; still honoured so existing setups keep working.
_ENV_OVERRIDE_LEGACY = ("AUDIOCONV_FFMPEG_DIR",)


def _env_override() -> str | None:
    for name in (_ENV_OVERRIDE, *_ENV_OVERRIDE_LEGACY):
        value = os.environ.get(name)
        if value:
            return value
    return None

#: ffmpeg's own ``-progress`` output reports this.
_TIME_US_RE = re.compile(r"^out_time_us=(-?\d+)$")
_TIME_RE = re.compile(r"^out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)$")

_PROCESS_LOCK = threading.Lock()
_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()
_POLL_SECONDS = 0.05


@contextmanager
def _null_slot():
    """Context manager matching :meth:`ProcessLimiter.slot` when unbounded."""
    yield


class ProcessLimiter:
    """Bound external ffmpeg/ffprobe processes for one conversion batch.

    A worker thread can wait for a slot without blocking the GUI thread, and a
    cancellation event can interrupt that wait before a subprocess is spawned.
    The limiter intentionally lives at the pipeline level so unrelated CLI or
    toolchain detection calls keep their existing behaviour.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._slots = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @contextmanager
    def slot(self, cancel: threading.Event | None = None):
        acquired = False
        try:
            while not acquired:
                if cancel is not None and cancel.is_set():
                    raise CancelledError("转换已取消")
                acquired = self._slots.acquire(timeout=_POLL_SECONDS)
            with self._lock:
                self._active += 1
            yield
        finally:
            if acquired:
                with self._lock:
                    self._active = max(0, self._active - 1)
                self._slots.release()


def _register_process(process: subprocess.Popen[str]) -> None:
    with _PROCESS_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister_process(process: subprocess.Popen[str]) -> None:
    with _PROCESS_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def _terminate_process(process: subprocess.Popen[str], *, grace: float = 3.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=grace)
        except OSError:
            pass


def terminate_active_processes(*, grace: float = 3.0) -> None:
    """Terminate every ffmpeg process currently owned by this application."""
    with _PROCESS_LOCK:
        processes = [process for process in _ACTIVE_PROCESSES if process.poll() is None]
    for process in processes:
        try:
            process.terminate()
        except OSError:
            pass

    deadline = time.monotonic() + grace
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


@dataclass(frozen=True)
class Toolchain:
    """The resolved pair of ffmpeg binaries plus what they can do."""

    ffmpeg: Path
    ffprobe: Path
    version: str
    has_libmp3lame: bool
    source: str
    #: ``(mtime, size)`` of the ffmpeg binary, used to validate a cached probe.
    fingerprint: tuple[float, int] | None = None

    def describe(self) -> str:
        lame = "libmp3lame ✓" if self.has_libmp3lame else "libmp3lame ✗"
        return f"{self.version} · {lame} · {self.source}"

    def to_cache(self) -> dict[str, object]:
        """A JSON-safe record of the probe, for :func:`find_toolchain`."""
        return {
            "ffmpeg": str(self.ffmpeg),
            "ffprobe": str(self.ffprobe),
            "version": self.version,
            "has_libmp3lame": self.has_libmp3lame,
            "source": self.source,
            "mtime": self.fingerprint[0] if self.fingerprint else None,
            "size": self.fingerprint[1] if self.fingerprint else None,
        }


def _fingerprint(path: Path) -> tuple[float, int]:
    stat = path.stat()
    return (stat.st_mtime, stat.st_size)


def _from_cache(cached: dict | None, ffmpeg: Path, ffprobe: Path) -> Toolchain | None:
    """Reuse a previous probe when the binary has not changed since."""
    if not cached:
        return None
    if cached.get("ffmpeg") != str(ffmpeg):
        return None
    try:
        fingerprint = _fingerprint(ffmpeg)
    except OSError:
        return None
    if cached.get("mtime") != fingerprint[0] or cached.get("size") != fingerprint[1]:
        return None
    return Toolchain(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        version=str(cached.get("version") or "unknown"),
        has_libmp3lame=bool(cached.get("has_libmp3lame")),
        source=str(cached.get("source") or "缓存"),
        fingerprint=fingerprint,
    )


def bundled_dirs() -> list[Path]:
    """Candidate directories that may hold a bundled ffmpeg build."""
    candidates: list[Path] = []
    explicit = _env_override()
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(root / "vendor" / "ffmpeg" for root in bundle_roots())
    return candidates


def _probe_version(ffmpeg: Path) -> str:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-version"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    first = (result.stdout or result.stderr).splitlines()
    return first[0].strip() if first else "unknown"


def _probe_lame(ffmpeg: Path) -> bool:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    return "libmp3lame" in (result.stdout + result.stderr)


def _describe_dir(directory: Path) -> str:
    return f"内置 ({directory})"


def find_toolchain(
    explicit_dir: str | os.PathLike[str] | None = None,
    *,
    cached: dict | None = None,
) -> Toolchain:
    """Locate ffmpeg and ffprobe.

    Search order: an explicit directory (from user settings), the
    ``ECHOSHIFT_FFMPEG_DIR`` environment variable, the bundled ``vendor/ffmpeg``
    directory, then ``PATH``.

    ``cached`` is a record from a previous :meth:`Toolchain.to_cache`; when the
    binary at that path is unchanged, its version and encoder list are reused
    instead of spawning two subprocesses.
    """
    tried: list[str] = []

    def _probe(ffmpeg: Path, ffprobe: Path, source: str) -> Toolchain | None:
        ffmpeg = ffmpeg.resolve()
        ffprobe = ffprobe.resolve()
        reused = _from_cache(cached, ffmpeg, ffprobe)
        if reused is not None:
            return reused
        try:
            return Toolchain(
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                version=_probe_version(ffmpeg),
                has_libmp3lame=_probe_lame(ffmpeg),
                source=source,
                fingerprint=_fingerprint(ffmpeg),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            tried.append(f"{ffmpeg} ({exc})")
            return None

    def _from_dir(directory: Path, source: str) -> Toolchain | None:
        ffmpeg = directory / _FFMPEG_EXE
        ffprobe = directory / _FFPROBE_EXE
        if ffmpeg.is_file() and ffprobe.is_file():
            return _probe(ffmpeg, ffprobe, source)
        tried.append(str(ffmpeg))
        return None

    search: list[tuple[Path, str]] = []
    if explicit_dir:
        search.append((Path(explicit_dir), f"自定义 ({explicit_dir})"))
    for directory in bundled_dirs():
        search.append((directory, _describe_dir(directory)))

    for directory, source in search:
        found = _from_dir(directory, source)
        if found is not None:
            return found

    ffmpeg_which = shutil.which("ffmpeg")
    ffprobe_which = shutil.which("ffprobe")
    if ffmpeg_which and ffprobe_which:
        found = _probe(Path(ffmpeg_which), Path(ffprobe_which), "PATH")
        if found is not None:
            return found

    raise ToolNotFoundError(
        "找不到 ffmpeg / ffprobe。\n"
        f"已尝试：{os.linesep.join(tried) if tried else '(无)'}\n"
        f"可在设置里手动指定目录，或设置环境变量 {_ENV_OVERRIDE}。"
    )


def _tail(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def run(
    cmd: Sequence[str],
    *,
    timeout: float | None = None,
    check: bool = True,
    cancel: threading.Event | None = None,
    process_limiter: ProcessLimiter | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with captured output and cooperative cancellation."""
    limiter = process_limiter
    slot = limiter.slot(cancel) if limiter is not None else _null_slot()
    with slot:
        process = subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        _register_process(process)
        started = time.monotonic()
        stdout = ""
        stderr = ""
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    _terminate_process(process)
                    raise CancelledError("转换已取消")
                if timeout is not None and time.monotonic() - started >= timeout:
                    _terminate_process(process)
                    raise EchoShiftError(f"命令执行超过 {timeout:g} 秒，已终止")
                try:
                    stdout, stderr = process.communicate(timeout=_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _terminate_process(process)
            raise
        finally:
            _unregister_process(process)

    result = subprocess.CompletedProcess(
        args=list(cmd), returncode=process.returncode, stdout=stdout, stderr=stderr
    )
    if check and result.returncode != 0:
        raise EchoShiftError(
            f"命令执行失败 (exit {result.returncode}): {' '.join(cmd[:3])}…\n"
            f"{_tail(result.stderr)}"
        )
    return result


def run_with_progress(
    cmd: Sequence[str],
    *,
    total_duration: float | None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    timeout: float | None = 120,
    process_limiter: ProcessLimiter | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg while reporting completion as a 0..1 fraction.

    ``cmd`` must already include ``-progress pipe:1 -nostats``.  Both pipes are
    drained on reader threads.  The caller polls their queues, keeping cancel
    and no-progress timeout checks responsive even while ffmpeg is silent.
    """
    limiter = process_limiter
    slot = limiter.slot(cancel) if limiter is not None else _null_slot()
    with slot:
        process = subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        _register_process(process)

        stderr_chunks: list[str] = []
        stdout_lines: queue.Queue[str | None] = queue.Queue()

        def _drain_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_chunks.append(line)
                if len(stderr_chunks) > 4000:
                    del stderr_chunks[:2000]

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        def _drain_stdout() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    stdout_lines.put(line)
            finally:
                stdout_lines.put(None)

        stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
        stdout_thread.start()

        last_activity = time.monotonic()
        stdout_done = False
        try:
            while not (stdout_done and process.poll() is not None):
                if cancel is not None and cancel.is_set():
                    _terminate_process(process)
                    raise CancelledError("转换已取消")
                if timeout is not None and time.monotonic() - last_activity >= timeout:
                    _terminate_process(process)
                    raise EchoShiftError(f"ffmpeg 连续 {timeout:g} 秒无进度，已终止")

                try:
                    line = stdout_lines.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    continue
                if line is None:
                    stdout_done = True
                    continue

                last_activity = time.monotonic()
                if on_progress is None or not total_duration:
                    continue
                line = line.strip()
                seconds: float | None = None
                match = _TIME_US_RE.match(line)
                if match:
                    value = int(match.group(1))
                    if value >= 0:
                        seconds = value / 1_000_000
                else:
                    match = _TIME_RE.match(line)
                    if match:
                        hours, minutes, secs = match.groups()
                        seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)
                if seconds is not None:
                    on_progress(max(0.0, min(1.0, seconds / total_duration)))
        except BaseException:
            _terminate_process(process)
            raise
        finally:
            _unregister_process(process)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)

        if cancel is not None and cancel.is_set():
            raise CancelledError("转换已取消")

        stderr = "".join(stderr_chunks)
        result = subprocess.CompletedProcess(
            args=list(cmd),
            returncode=process.returncode,
            stdout="",
            stderr=stderr,
        )
        if process.returncode != 0:
            raise EchoShiftError(
                f"ffmpeg 执行失败 (exit {process.returncode})\n{_tail(stderr)}"
            )
        return result


def ffprobe_json(
    ffprobe: Path,
    path: Path | str,
    *,
    timeout: float = 120,
    cancel: threading.Event | None = None,
    process_limiter: ProcessLimiter | None = None,
) -> dict:
    """Run ffprobe and return its JSON document."""
    import json

    result = run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=timeout,
        check=False,
        cancel=cancel,
        process_limiter=process_limiter,
    )
    if result.returncode != 0:
        raise ProbeError(
            f"ffprobe 无法读取 {Path(path).name}"
            "（ffmpeg 不认识这个格式，或者文件损坏／仍是加密状态）\n"
            f"{_tail(result.stderr)}"
        )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe 输出无法解析: {exc}") from exc


def iter_lines(text: str) -> Iterable[str]:
    return (line for line in text.splitlines() if line.strip())
