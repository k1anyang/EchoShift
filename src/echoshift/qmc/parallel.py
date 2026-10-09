"""Decrypt a large payload across several processes.

The QMC2 RC4 cipher re-keys itself once per 5120-byte segment from the *same*
initial S-box, so a segment's keystream depends on nothing but its own index.
That makes byte ranges independently decryptable -- which matters because the
pure-Python RC4 inner loop only manages roughly 2 MB/s, and a 40 MB lossless
track would otherwise spend half a minute in the decrypt step alone.

QMC1 and the QMC2 Map cipher are offset-addressable too, so they get the same
treatment for free.

Everything here is best-effort and observable:

* :func:`pool_available` reports whether a process pool could be created.  Some
  locked-down Windows environments refuse the named pipes
  ``multiprocessing`` needs, and the answer is cached so the cost is paid once.
* A failure to start the pool, or an unusable worker error, returns
  ``(False, reason)`` so the caller falls back to the serial loop.
* A *wrong key* is different: the head probe raises
  :class:`~echoshift.errors.EchoShiftError`, which propagates instead of being
  swallowed, because retrying serially would only waste time.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..errors import EchoShiftError
from .qmc1 import decrypt_in_place as qmc1_decrypt_in_place
from .qmc2 import OTHER_SEGMENT_SIZE, Qmc2Crypto

__all__ = [
    "MIN_PARALLEL_BYTES",
    "CHUNK_BYTES",
    "MAX_WORKERS",
    "plan_chunks",
    "decrypt_parallel",
    "pool_available",
    "pool_unavailable_reason",
    "reset_pool_probe",
    "reset_shared_pool",
]

#: Below this size the process pool costs more than it saves.
MIN_PARALLEL_BYTES = 1 << 20
#: Target size of one work item.
CHUNK_BYTES = 4 << 20
#: Never start more processes than this, however many cores exist.
MAX_WORKERS = 8

_pool_lock = threading.Lock()
_pool_state: bool | None = None
_pool_reason: str | None = None

#: One process pool, shared by every container in a batch.
#:
#: A pool per container looks tidy but is expensive and, in a batch, lands as a
#: burst of process creation on top of whatever ffmpeg is already doing.
#: Reusing one pool also means the *total* number of decrypt processes is a
#: single bounded number instead of "containers in flight times workers each".
_shared_pool: "ProcessPoolExecutor | None" = None
_shared_pool_workers = 0
#: Serialises submission and collection on the shared pool, so concurrent
#: decryptions queue behind each other instead of interleaving.
_shared_pool_lock = threading.Lock()
#: Guards the pool's identity and lifetime.  It must be a *separate* lock:
#: ``_acquire_shared_pool`` is called while ``_shared_pool_lock`` is already
#: held, so reusing that one would deadlock against itself.
_shared_pool_lifecycle_lock = threading.Lock()
_shared_broken = False


def reset_pool_probe() -> None:
    """Forget the cached answer (used by tests)."""
    global _pool_state, _pool_reason
    with _pool_lock:
        _pool_state = None
        _pool_reason = None


def reset_shared_pool() -> None:
    """Drop the shared pool; the next decryption creates a fresh one."""
    global _shared_pool, _shared_pool_workers, _shared_broken
    with _shared_pool_lifecycle_lock:
        pool = _shared_pool
        _shared_pool = None
        _shared_pool_workers = 0
        _shared_broken = False
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass


def _acquire_shared_pool(workers: int) -> "ProcessPoolExecutor":
    """Return the shared pool, sizing (or rebuilding) it for ``workers``.

    Callers hold the submission lock; this takes the separate lifecycle lock.
    """
    global _shared_pool, _shared_pool_workers, _shared_broken
    with _shared_pool_lifecycle_lock:
        if _shared_broken:
            _shared_broken = False
            _shared_pool = None
        if _shared_pool is None or _shared_pool_workers != workers:
            if _shared_pool is not None:
                try:
                    _shared_pool.shutdown(wait=False, cancel_futures=True)
                except Exception:  # noqa: BLE001
                    pass
            _shared_pool = ProcessPoolExecutor(max_workers=workers)
            _shared_pool_workers = workers
        assert _shared_pool is not None
        return _shared_pool


def _mark_shared_pool_broken() -> None:
    global _shared_broken
    with _shared_pool_lifecycle_lock:
        _shared_broken = True


def pool_available() -> bool:
    """Whether a process pool can be created here; the answer is cached."""
    global _pool_state, _pool_reason
    with _pool_lock:
        if _pool_state is None:
            # ``_probe_pool`` must not take ``_pool_lock``: it is called with
            # the lock already held, and a plain Lock is not reentrant -- doing
            # so deadlocked the very first decryption for ever.
            _pool_state, _pool_reason = _probe_pool()
        return _pool_state


def pool_unavailable_reason() -> str | None:
    """Why the probe failed, once it has run.  ``None`` when it succeeded.

    Without this the only symptom of an unusable pool is a vague "改用单进程"
    warning, which hides the difference between an environment that forbids
    ``multiprocessing`` and a probe that simply timed out under load.
    """
    with _pool_lock:
        return _pool_reason


def _probe_pool() -> tuple[bool, str | None]:
    """Try to spawn one worker process.  Returns ``(usable, failure_reason)``.

    Pure with respect to module state: the caller owns the caching and the lock.
    """
    try:
        # 30 s is generous for spawning one worker, but a machine under heavy
        # load (or an image being made while a batch runs) can exceed it -- and
        # then *every* decryption silently falls back to serial for the rest of
        # the session, because the answer is cached.
        with ProcessPoolExecutor(max_workers=1) as pool:
            pool.submit(int).result(timeout=30)
        return True, None
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable here"
        return False, f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True)
class _Chunk:
    source: str
    destination: str
    key: bytes | None
    is_qmc1: bool
    offset: int
    length: int


def run_chunk(chunk: _Chunk) -> tuple[int, bytes | None, str | None]:
    """Decrypt one byte range straight into the destination file.

    This is the function that executes in a worker process, but it is also
    usable in-process -- which is how the test suite verifies the chunking
    arithmetic without needing a pool.
    """
    try:
        with open(chunk.source, "rb") as handle:
            handle.seek(chunk.offset)
            buffer = bytearray(handle.read(chunk.length))
        if len(buffer) != chunk.length:
            return chunk.offset, None, f"短读：期望 {chunk.length} 字节，得到 {len(buffer)}"

        if chunk.is_qmc1:
            qmc1_decrypt_in_place(buffer, chunk.offset)
        else:
            if not chunk.key:
                return chunk.offset, None, "缺少密钥"
            Qmc2Crypto(chunk.key).decrypt(buffer, chunk.offset)

        head = bytes(buffer[:16]) if chunk.offset == 0 else None
        with open(chunk.destination, "r+b") as handle:
            handle.seek(chunk.offset)
            handle.write(buffer)
        return chunk.offset, head, None
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        return chunk.offset, None, f"{type(exc).__name__}: {exc}"


def plan_chunks(
    total: int, *, chunk_bytes: int = CHUNK_BYTES, align: int = 1
) -> list[tuple[int, int]]:
    """Split ``total`` bytes into ``(offset, length)`` spans.

    When ``align`` is greater than one, every span except the last starts on a
    multiple of it -- which keeps RC4 chunks segment-aligned so no worker
    repeats another's key-schedule work.
    """
    if total <= 0:
        return []
    if align <= 1:
        return [
            (offset, min(chunk_bytes, total - offset))
            for offset in range(0, total, chunk_bytes)
        ]

    spans: list[tuple[int, int]] = []
    offset = 0
    while offset < total:
        length = min(chunk_bytes, total - offset)
        if offset + length < total:
            remainder = length % align
            if remainder:
                length += align - remainder
        length = min(length, total - offset)
        spans.append((offset, length))
        offset += length
    return spans


def build_chunks(
    *,
    source: Path,
    destination: Path,
    total: int,
    key: bytes | None,
    is_qmc1: bool,
    chunk_bytes: int = CHUNK_BYTES,
) -> list[_Chunk]:
    """The work items :func:`decrypt_parallel` would dispatch."""
    align = 1 if is_qmc1 else OTHER_SEGMENT_SIZE
    return [
        _Chunk(
            source=str(source),
            destination=str(destination),
            key=key,
            is_qmc1=is_qmc1,
            offset=offset,
            length=length,
        )
        for offset, length in plan_chunks(total, chunk_bytes=chunk_bytes, align=align)
    ]


def decrypt_parallel(
    *,
    source: Path,
    destination: Path,
    total: int,
    key: bytes | None,
    is_qmc1: bool,
    workers: int,
    chunk_bytes: int | None = None,
    on_progress: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    head_probe: Callable[[bytes], None] | None = None,
) -> tuple[bool, str | None, EchoShiftError | None]:
    """Decrypt ``source`` into ``destination`` using ``workers`` processes.

    Returns ``(handled, error, probe_error)``.

    * ``handled`` is ``True`` when the pool did the work.
    * ``handled`` is ``False`` with ``error`` set when the caller should fall
      back to serial decryption, and ``error`` explains why.
    * ``probe_error`` is the exception ``head_probe`` raised -- a wrong key.
      It is handed back rather than raised because the caller owns the
      destination file and has to remove it; returning it also stops the caller
      from falling back to a serial pass that would decrypt the whole file only
      to fail the same check.  Chunks already dispatched are still awaited
      before this returns, so nothing is writing to the destination afterwards.

    The process pool is shared across every container in the process, so the
    number of decrypt processes is capped by the pool size rather than growing
    with the number of files being converted at once.
    """
    if workers <= 1 or total < MIN_PARALLEL_BYTES:
        return False, None, None
    if not pool_available():
        detail = pool_unavailable_reason()
        return (
            False,
            f"当前环境不允许创建子进程（进程池不可用：{detail}）" if detail
            else "当前环境不允许创建子进程（进程池不可用）",
            None,
        )

    workers = min(workers, MAX_WORKERS, max(1, os.cpu_count() or 2))
    chunks = build_chunks(
        source=source,
        destination=destination,
        total=total,
        key=key,
        is_qmc1=is_qmc1,
        chunk_bytes=chunk_bytes or CHUNK_BYTES,
    )
    if len(chunks) < 2:
        return False, None, None

    try:
        # Reserve the full output so workers can seek and write independently.
        with open(destination, "wb") as handle:
            handle.truncate(total)

        completed = 0
        bad_key: EchoShiftError | None = None
        failure: tuple[str, int] | None = None
        cancelled = False

        # One loop, one exit.  Returning early while chunks are still running
        # leaves workers writing into the destination after the caller has been
        # told to clean it up -- which is how a rejected key left a partial file
        # behind that reappeared after deletion.
        #
        # Hold the lock for the whole submission+collection window: concurrent
        # decryptions then queue up behind each other instead of interleaving
        # twice the number of processes the budget allows.
        with _shared_pool_lock:
            pool = _acquire_shared_pool(workers)
            for offset, head, error in pool.map(run_chunk, chunks):
                if error:
                    failure = (error, offset)
                    continue
                if head is not None and head_probe is not None:
                    # Chunk 0 arrives first, so a bad key is known immediately;
                    # keep draining so nothing is still writing when we return.
                    if bad_key is None:
                        try:
                            head_probe(head)
                        except EchoShiftError as exc:
                            bad_key = exc
                    # A chunk that failed the probe is not progress towards a
                    # usable output; one that passed still is, so fall through.
                    if bad_key is not None:
                        continue
                completed += 1
                if on_progress is not None:
                    on_progress(completed / len(chunks))
                if should_stop is not None and should_stop():
                    cancelled = True

        if bad_key is not None:
            # The caller owns the destination and removes it; reporting a
            # fallback reason instead made it decrypt the whole file again only
            # to fail the same check.
            return False, None, bad_key
        if failure is not None:
            error, offset = failure
            return False, f"并行解密失败（offset {offset}）：{error}", None
        if cancelled:
            return False, "已取消", None
        return True, None, None
    except EchoShiftError as exc:
        return False, None, exc
    except (BrokenProcessPool, OSError) as exc:
        # A dead pool must not poison every later container in the batch.
        _mark_shared_pool_broken()
        return False, f"{type(exc).__name__}: {exc}", None
    except Exception as exc:  # noqa: BLE001 - caller falls back to serial
        return False, f"{type(exc).__name__}: {exc}", None
