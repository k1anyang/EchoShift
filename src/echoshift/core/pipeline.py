"""The conversion pipeline: decrypt, probe, encode, verify.

One :class:`JobResult` describes exactly what happened to one input file, so
the CLI and the GUI can both render the same truth without re-deriving it.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..qmc.decoder import (
    ContainerInfo,
    PreparedInput,
    is_supported_path,
    looks_like_audio,
    prepare_input,
)
from ..qmc.keystore import KeyStore
from .args import build_ffmpeg_args
from ..errors import EchoShiftError, CancelledError
from .ffmpeg import ProcessLimiter, Toolchain, run_with_progress
from .naming import DEFAULT_TEMPLATE, render_output_path
from .probe import MediaInfo, probe
from .settings import EncodeSettings, ResolvedPlan
from .verify import VerificationReport, verify_output

__all__ = [
    "JobState",
    "OverwritePolicy",
    "JobResult",
    "Pipeline",
    "ProgressFn",
    "ResultFn",
    "default_work_dir",
    "effective_workers",
    "decrypt_process_budget",
]

MAX_BATCH_WORKERS = 4
#: Ceiling on decryption *processes* for a whole batch, independent of how many
#: files run at once.  This is the limit that matters: the ciphers are pure
#: Python, so decryption has to happen in processes, but an unbounded number of
#: them starves every other thread on the machine.
MAX_DECRYPT_PROCESSES = 4
#: Decryption processes one container is allowed when nothing competes with it.
MAX_DECRYPT_WORKERS_PER_FILE = 2
_PROGRESS_MIN_INTERVAL = 0.12

ProgressFn = Callable[[float, str], None]
ResultFn = Callable[[int, "JobResult"], None]


def effective_workers(requested: int, *, cpu_count: int | None = None) -> int:
    """Return a conservative file-level parallelism limit.

    MP3 conversion is an external-process workload.  Allowing one process per
    logical CPU is counterproductive because each job also performs probing,
    optional full verification and filesystem I/O.  Half the available CPUs,
    capped at four, is a safer ceiling while preserving an explicit request of
    one for the most constrained machines.
    """
    requested = max(1, int(requested))
    cpus = max(1, int(cpu_count or (os.cpu_count() or 2)))
    return max(1, min(requested, MAX_BATCH_WORKERS, max(1, cpus // 2)))


def decrypt_process_budget(workers: int, *, cpu_count: int | None = None) -> int:
    """Return the total decryption-process budget for a whole batch.

    Every QMC cipher is a pure-Python byte loop, so a decryption that runs
    inside a worker *thread* holds the GIL in ~5 ms slices and starves every
    other Python thread in the process -- including Tk's event loop, which is
    what makes the window stop responding while a batch runs.  Decryption
    therefore has to happen in processes.

    The budget is deliberately tied to the *file* parallelism rather than to
    the number of queued files: two processes per concurrently converting file
    keeps a single decryption ahead of the encoder without letting a large
    queue multiply the process count.
    """
    cpus = max(1, int(cpu_count or (os.cpu_count() or 2)))
    ceiling = max(1, min(MAX_DECRYPT_PROCESSES, cpus // 2))
    files = effective_workers(workers, cpu_count=cpus)
    return max(
        1,
        min(
            ceiling,
            files * MAX_DECRYPT_WORKERS_PER_FILE,
        ),
    )


class JobState(str, Enum):
    PENDING = "pending"
    DECRYPTING = "decrypting"
    ENCODING = "encoding"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            JobState.PENDING: "等待中",
            JobState.DECRYPTING: "解密中",
            JobState.ENCODING: "转码中",
            JobState.VERIFYING: "校验中",
            JobState.DONE: "完成",
            JobState.FAILED: "失败",
            JobState.SKIPPED: "跳过",
            JobState.CANCELLED: "已取消",
        }[self]

    @property
    def is_terminal(self) -> bool:
        return self in (
            JobState.DONE,
            JobState.FAILED,
            JobState.SKIPPED,
            JobState.CANCELLED,
        )

    @property
    def is_success(self) -> bool:
        return self is JobState.DONE


class OverwritePolicy(str, Enum):
    RENAME = "rename"
    OVERWRITE = "overwrite"
    SKIP = "skip"

    @property
    def label(self) -> str:
        return {
            OverwritePolicy.RENAME: "自动改名（不覆盖）",
            OverwritePolicy.OVERWRITE: "覆盖同名文件",
            OverwritePolicy.SKIP: "跳过已存在文件",
        }[self]


def default_work_dir() -> Path:
    """Pick a writable scratch directory for decrypted intermediates.

    ``%TEMP%`` is the natural choice, but locked-down machines and sandboxes
    sometimes refuse it, so each candidate is probed before being adopted.
    """
    candidates: list[Path] = []
    override = os.environ.get("ECHOSHIFT_TEMP") or os.environ.get("AUDIOCONV_TEMP")
    if override:
        candidates.append(Path(override))
    candidates.append(Path(tempfile.gettempdir()) / "echoshift")
    candidates.append(Path.home() / ".echoshift" / "work")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_bytes(b"")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir())


@dataclass
class JobResult:
    """Everything that happened to one input file."""

    source: Path
    state: JobState
    output: Path | None = None
    message: str = ""
    plan: ResolvedPlan | None = None
    source_info: MediaInfo | None = None
    output_info: MediaInfo | None = None
    report: VerificationReport | None = None
    container: ContainerInfo | None = None
    warnings: tuple[str, ...] = ()
    attempts: int = 0
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.state.is_success

    def summary(self) -> str:
        if self.state is JobState.DONE:
            ratio = ""
            if self.source_info and self.source_info.size and self.output:
                try:
                    saved = 1 - self.output.stat().st_size / self.source_info.size
                    ratio = f"（体积 {saved * 100:+.0f}%）"
                except OSError:
                    ratio = ""
            return f"完成{ratio} · {self.elapsed:.1f}s"
        return f"{self.state.label} · {self.message}" if self.message else self.state.label


@dataclass
class PipelineOptions:
    """Everything that influences a run but is not a per-file property."""

    settings: EncodeSettings
    template: str = DEFAULT_TEMPLATE
    output_dir: Path | None = None
    overwrite: OverwritePolicy = OverwritePolicy.RENAME
    verify: bool = True
    deep_verify: bool = True
    retries: int = 1
    ekey: str | None = None
    work_dir: Path = field(default_factory=default_work_dir)
    keep_temp: bool = False
    #: Processes used to decrypt one large container; 0 means "decide for me".
    decrypt_workers: int = 0


class Pipeline:
    """Runs conversions, one job at a time."""

    def __init__(
        self,
        toolchain: Toolchain,
        options: PipelineOptions,
        *,
        keystore: KeyStore | None = None,
        logger: Callable[[str], None] | None = None,
        process_limiter: ProcessLimiter | None = None,
    ) -> None:
        self.toolchain = toolchain
        self.options = options
        self.keystore = keystore
        self.log = logger or (lambda _message: None)
        self._reserved: set[Path] = set()
        self._reserved_lock = threading.Lock()
        requested_decrypt_workers = options.decrypt_workers or min(
            2, max(1, (os.cpu_count() or 2) // 2)
        )
        self.decrypt_workers = min(2, max(1, int(requested_decrypt_workers)))
        self.process_limiter = process_limiter or ProcessLimiter(2)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _reserve(self, path: Path) -> None:
        with self._reserved_lock:
            self._reserved.add(path)

    def _is_reserved(self, path: Path) -> bool:
        with self._reserved_lock:
            return path in self._reserved

    def _same_file_exists(self, candidate: Path) -> bool:
        """Whether ``candidate`` already exists, ignoring name case.

        ``Path.exists()`` is case-sensitive even on Windows, where the
        filesystem is not: a template that renders ``Song.mp3`` while
        ``song.mp3`` is on disk would look like a free name and the "rename"
        policy would quietly produce a second file (or, under "overwrite",
        replace one the user never named).
        """
        if candidate.exists():
            return True
        try:
            names = os.listdir(candidate.parent)
        except OSError:
            return False
        return os.path.normcase(candidate.name) in {os.path.normcase(name) for name in names}

    def _pick_output(self, desired: Path) -> tuple[Path, bool]:
        """Apply the overwrite policy; return ``(path, should_process)``."""

        def taken(candidate: Path) -> bool:
            return self._same_file_exists(candidate) or self._is_reserved(candidate)

        policy = self.options.overwrite
        if not taken(desired):
            return desired, True

        if policy is OverwritePolicy.OVERWRITE:
            return desired, True
        if policy is OverwritePolicy.SKIP:
            return desired, False

        stem, suffix, parent = desired.stem, desired.suffix, desired.parent
        for counter in range(2, 1000):
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not taken(candidate):
                return candidate, True
        raise EchoShiftError(f"无法为 {desired.name} 找到可用的输出文件名")

    def _output_root(self, source: Path) -> Path:
        return Path(self.options.output_dir) if self.options.output_dir else source.parent

    # ------------------------------------------------------------------ #
    # the job itself
    # ------------------------------------------------------------------ #

    def run_job(
        self,
        source: Path | str,
        *,
        index: int = 1,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
        decrypt_budget: int | None = None,
    ) -> JobResult:
        """Convert one file, never raising: failures come back as state.

        ``decrypt_budget`` caps how many processes this job may use for
        decryption.  ``run_batch`` passes the share of the batch-wide budget;
        leaving it unset keeps the operator's own ``decrypt_workers`` setting.
        """
        source = Path(source)
        started = time.perf_counter()
        result = JobResult(source=source, state=JobState.PENDING)
        prepared: PreparedInput | None = None
        partial_output: Path | None = None
        # The budget can only ever tighten the configured value: an operator who
        # asked for single-process decryption is never overruled by it.
        job_decrypt_workers = self.decrypt_workers
        if decrypt_budget is not None:
            job_decrypt_workers = max(1, min(job_decrypt_workers, int(decrypt_budget)))

        last_report_at = 0.0
        last_report_fraction = -1.0
        last_report_stage = ""

        def report(fraction: float, stage: str) -> None:
            nonlocal last_report_at, last_report_fraction, last_report_stage
            if on_progress is not None:
                fraction = max(0.0, min(1.0, fraction))
                now = time.monotonic()
                changed_stage = stage != last_report_stage
                terminal = fraction >= 1.0
                if (
                    changed_stage
                    or terminal
                    or now - last_report_at >= _PROGRESS_MIN_INTERVAL
                    or fraction - last_report_fraction >= 0.01
                ):
                    on_progress(fraction, stage)
                    last_report_at = now
                    last_report_fraction = fraction
                    last_report_stage = stage

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        try:
            if not source.is_file():
                raise EchoShiftError(f"文件不存在：{source}")
            # No extension gate here on purpose: ffprobe is the authority on
            # whether a file is decodable, and refusing early would reject
            # perfectly convertible inputs with unfamiliar extensions.

            # --- 1. decrypt if needed -------------------------------------
            if cancelled():
                raise CancelledError("已取消")
            result.state = JobState.DECRYPTING
            report(0.01, "解密中")
            self.log(f"[{index}] 读取 {source.name}")

            prepared = prepare_input(
                source,
                self.options.work_dir,
                keystore=self.keystore,
                ekey=self.options.ekey,
                workers=job_decrypt_workers,
                on_progress=lambda f: report(0.01 + 0.20 * f, "解密中"),
                on_warning=lambda text: self.log(f"[{index}] {text}"),
                cancel=cancel,
            )
            result.container = prepared.container
            if prepared.container is not None:
                self.log(f"[{index}] {prepared.container.describe()}")

            # --- 2. probe the decodable file ------------------------------
            source_info = probe(
                self.toolchain.ffprobe,
                prepared.path,
                cancel=cancel,
                process_limiter=self.process_limiter,
            )
            result.source_info = source_info
            self.log(f"[{index}] 源：{source_info.describe()}")

            # --- 3. resolve settings against this source ------------------
            plan = self.options.settings.resolve(
                source_info.audio.sample_rate if source_info.audio else None,
                source_info.audio.channels if source_info.audio else None,
            )
            result.plan = plan
            result.warnings = plan.warnings
            for warning in plan.warnings:
                self.log(f"[{index}] 提示：{warning}")

            # --- 4. decide where it goes ----------------------------------
            desired = render_output_path(
                self.options.template,
                source_info,
                plan,
                self._output_root(source),
                index=index,
                original_path=source,
            )
            output, proceed = self._pick_output(desired)
            result.output = output
            if not proceed:
                result.state = JobState.SKIPPED
                result.message = f"已存在 {output.name}"
                self.log(f"[{index}] 跳过（已存在）：{output}")
                result.elapsed = time.perf_counter() - started
                return result
            self._reserve(output)

            # --- 5. encode (with retries) ---------------------------------
            output.parent.mkdir(parents=True, exist_ok=True)
            partial_output = output.with_name(
                f".{output.stem}.echoshift-{os.getpid()}-"
                f"{threading.get_ident()}.part{output.suffix}"
            )
            last_error: Exception | None = None
            attempts = max(1, self.options.retries + 1)
            for attempt in range(1, attempts + 1):
                if cancelled():
                    raise CancelledError("已取消")
                result.attempts = attempt
                result.state = JobState.ENCODING
                report(0.25, "转码中")
                try:
                    partial_output.unlink(missing_ok=True)
                    self._encode(
                        prepared.path, partial_output, plan, index,
                        on_progress=lambda f: report(0.25 + 0.65 * f, "转码中"),
                        source_duration=source_info.duration,
                        cancel=cancel,
                    )
                    last_error = None
                    break
                except CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - retried below
                    last_error = exc
                    self.log(f"[{index}] 第 {attempt}/{attempts} 次转码失败：{exc}")
                    if attempt < attempts:
                        time.sleep(min(2.0, 0.5 * attempt))
            if last_error is not None:
                raise last_error

            # --- 6. verify ------------------------------------------------
            if self.options.verify:
                result.state = JobState.VERIFYING
                report(0.93, "校验中")
                report_obj = verify_output(
                    self.toolchain,
                    source_info,
                    partial_output,
                    self.options.settings,
                    plan,
                    deep=self.options.deep_verify,
                    cancel=cancel,
                    process_limiter=self.process_limiter,
                    display_name=output.name,
                )
                result.report = report_obj
                result.output_info = report_obj.info
                if not report_obj.ok:
                    raise EchoShiftError(f"校验未通过：{report_obj.summary()}")
                self.log(f"[{index}] {report_obj.summary()}")
            else:
                result.output_info = probe(
                    self.toolchain.ffprobe,
                    partial_output,
                    cancel=cancel,
                    process_limiter=self.process_limiter,
                )

            # A failed or cancelled run never replaces a valid destination.
            os.replace(partial_output, output)
            partial_output = None

            result.state = JobState.DONE
            report(1.0, "完成")
            self.log(f"[{index}] 输出：{output}")

        except CancelledError as exc:
            result.state = JobState.CANCELLED
            result.message = str(exc) or "已取消"
        except EchoShiftError as exc:
            result.state = JobState.FAILED
            result.message = str(exc)
            self.log(f"[{index}] 失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - never let a job escape
            result.state = JobState.FAILED
            result.message = f"{type(exc).__name__}: {exc}"
            self.log(f"[{index}] 未预期错误：{result.message}")
        finally:
            if partial_output is not None:
                try:
                    partial_output.unlink(missing_ok=True)
                except OSError:
                    pass
            if prepared is not None:
                if self.options.keep_temp:
                    self.log(f"[{index}] 保留临时文件：{prepared.path}")
                else:
                    prepared.cleanup()
            result.elapsed = time.perf_counter() - started
            if result.state is JobState.DONE:
                result.message = result.summary()

        return result

    def _encode(
        self,
        input_path: Path,
        output: Path,
        plan: ResolvedPlan,
        index: int,
        *,
        on_progress: Callable[[float], None],
        source_duration: float | None = None,
        cancel: threading.Event | None,
    ) -> None:
        duration = source_duration

        args = build_ffmpeg_args(
            self.toolchain.ffmpeg,
            input_path,
            output,
            self.options.settings,
            plan,
            overwrite=True,
        )
        if duration:
            self.log(f"[{index}] ffmpeg -q/-b 参数：{plan.describe()}")
        run_with_progress(
            args,
            total_duration=duration,
            on_progress=on_progress,
            cancel=cancel,
            process_limiter=self.process_limiter,
        )

    def _resolve_labels(
        self, count: int, index_labels: Sequence[int] | None
    ) -> list[int]:
        """Numbers to show for the sources of one batch.

        A caller-supplied list must line up with ``sources``; anything else
        would reintroduce the mislabelling this exists to prevent, so a
        mismatched length falls back to batch-local numbering.
        """
        if index_labels is not None:
            labels = [int(label) for label in index_labels]
            if len(labels) == count:
                return labels
        return list(range(1, count + 1))

    # ------------------------------------------------------------------ #
    # batch
    # ------------------------------------------------------------------ #

    def run_batch(
        self,
        sources: Sequence[Path],
        *,
        on_result: ResultFn | None = None,
        on_progress: Callable[[int, float, str], None] | None = None,
        cancel: threading.Event | None = None,
        workers: int = 1,
        index_labels: Sequence[int] | None = None,
    ) -> list[JobResult]:
        """Convert many files, preserving input order in the result list.

        ``index_labels`` gives the number to show for each source in logs and
        results.  Without it the numbers are positions inside this batch, so
        converting a three-file subset of a large queue would log them as
        ``[1]``-``[3]`` and send anyone reading the log to the wrong file.  The
        GUI passes the real queue positions; the CLI leaves it unset.
        """
        results: list[JobResult | None] = [None] * len(sources)
        workers = effective_workers(workers)
        labels = self._resolve_labels(len(sources), index_labels)
        # External processes are the expensive part of a job.  Keep at most
        # two active across encoding, probing and deep verification even when
        # the worker pool itself is larger.
        # The limiter is shared with optional background probes in the GUI;
        # keep its fixed two-process ceiling instead of replacing it here.

        # Decryption is pure Python, so running it *inside* several worker
        # threads at once makes every one of them hold the GIL in turn: the CPU
        # stays idle, the batch takes several times longer, and Tk's event loop
        # is starved until the window stops responding.  Hand the batch's
        # decryption-process budget to each job instead; the per-file override
        # that used to live here is what caused exactly that stall.
        decrypt_budget = decrypt_process_budget(workers)

        def work(position: int, source: Path) -> None:
            job = self.run_job(
                source,
                index=labels[position],
                on_progress=(
                    (lambda f, stage: on_progress(position, f, stage))
                    if on_progress
                    else None
                ),
                cancel=cancel,
                decrypt_budget=decrypt_budget,
            )
            results[position] = job
            if on_result is not None:
                on_result(position, job)

        if workers <= 1 or len(sources) <= 1:
            for position, source in enumerate(sources):
                if cancel is not None and cancel.is_set():
                    for index in range(position, len(sources)):
                        results[index] = JobResult(
                            source=sources[index],
                            state=JobState.CANCELLED,
                            message="已取消",
                        )
                    break
                work(position, source)
        else:
            # Keep only a small number of futures in flight.  Submitting every
            # file at once is needlessly expensive for very large queues and
            # makes cancellation and event delivery bursty.
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="echoshift") as pool:
                pending: dict[object, int] = {}
                next_position = 0

                def submit_next() -> None:
                    nonlocal next_position
                    while (
                        next_position < len(sources)
                        and len(pending) < workers
                        and not (cancel is not None and cancel.is_set())
                    ):
                        position = next_position
                        next_position += 1
                        future = pool.submit(work, position, sources[position])
                        pending[future] = position

                submit_next()
                while pending:
                    done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                    for future in done:
                        pending.pop(future, None)
                        future.result()
                    submit_next()

                if cancel is not None and cancel.is_set():
                    for position in range(next_position, len(sources)):
                        results[position] = JobResult(
                            source=sources[position],
                            state=JobState.CANCELLED,
                            message="已取消",
                        )

        return [r for r in results if r is not None]


def collect_sources(
    paths: Iterable[Path | str],
    *,
    recursive: bool = True,
) -> list[Path]:
    """Expand files and directories into a de-duplicated, sorted file list.

    Two different policies apply, and the difference matters:

    * A file the user named **explicitly** (drag-and-drop, file dialog, command
      line) is always accepted.  ffprobe decides whether it is convertible, so
      an unfamiliar or missing extension is not a reason to refuse it.
    * Files found by **walking a directory** are filtered, otherwise a music
      folder's ``.jpg``/``.cue``/``.log`` companions would all be queued.  The
      filter accepts known extensions *or* anything whose leading bytes look
      like a media container, so a mislabelled file is still picked up.
    """
    seen: dict[Path, None] = {}
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for child in sorted(iterator):
                if not child.is_file():
                    continue
                if is_supported_path(child) or looks_like_audio(child):
                    seen.setdefault(child.resolve(), None)
        elif path.is_file():
            seen.setdefault(path.resolve(), None)
    return list(seen)
