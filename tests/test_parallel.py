"""Chunk planning and multi-process decryption.

Each RC4 segment is generated from the same initial S-box, so byte ranges are
independently decryptable.  Two properties matter and are tested separately:

1. **The chunk arithmetic is right.**  ``build_chunks`` + ``run_chunk`` hold the
   entire algorithm; they are exercised in-process here so the correctness
   proof does not depend on being able to spawn a pool.
2. **The pool is used when it is available, and degrades cleanly when it is
   not.**  Some locked-down Windows environments refuse the named pipes
   ``multiprocessing`` needs, so the fallback is asserted rather than assumed.
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest

from echoshift.errors import DecryptionError
from echoshift.qmc import decoder as decoder_module
from echoshift.qmc import parallel
from echoshift.qmc.decoder import inspect, prepare_input
from echoshift.qmc.keystore import KeyStore
from echoshift.qmc.parallel import (
    CHUNK_BYTES,
    build_chunks,
    plan_chunks,
    pool_available,
    run_chunk,
)
from echoshift.qmc.qmc2 import OTHER_SEGMENT_SIZE, Qmc2Crypto, parse_ekey

# --------------------------------------------------------------------------- #
# plan_chunks
# --------------------------------------------------------------------------- #


def test_plan_chunks_splits_evenly():
    assert plan_chunks(100, chunk_bytes=40) == [(0, 40), (40, 40), (80, 20)]


def test_plan_chunks_covers_every_byte_exactly_once():
    for total in (1, 7, 40, 41, 999, 100_000):
        spans = plan_chunks(total, chunk_bytes=64)
        assert sum(length for _, length in spans) == total
        cursor = 0
        for offset, length in spans:
            assert offset == cursor
            assert length > 0
            cursor += length


def test_plan_chunks_aligns_interior_boundaries():
    align = OTHER_SEGMENT_SIZE
    spans = plan_chunks(align * 10 + 123, chunk_bytes=align * 3, align=align)
    assert sum(length for _, length in spans) == align * 10 + 123
    for offset, length in spans[:-1]:
        assert offset % align == 0, f"span at {offset} is not segment aligned"
        assert length % align == 0


def test_plan_chunks_handles_empty_and_tiny_inputs():
    assert plan_chunks(0) == []
    assert plan_chunks(-5) == []
    assert plan_chunks(3, chunk_bytes=CHUNK_BYTES) == [(0, 3)]


# --------------------------------------------------------------------------- #
# worker code, driven in-process
# --------------------------------------------------------------------------- #

RC4_KEY = bytes((i * 31 + 5) & 0xFF for i in range(512))
MAP_KEY = bytes((i * 7 + 11) & 0xFF for i in range(128))
PAYLOAD_SIZE = 700_000


def _payload(size: int = PAYLOAD_SIZE) -> bytes:
    """A FLAC-signature-prefixed payload that is cheap to build and varies."""
    block = bytes(range(256)) * 4
    body = (block * (size // len(block) + 1))[: size - 4]
    return b"fLaC" + body


def _write_qmc2(path: Path, payload: bytes, key: bytes) -> None:
    buffer = bytearray(payload)
    Qmc2Crypto(key).decrypt(buffer, 0)
    path.write_bytes(bytes(buffer) + key + struct.pack("<I", len(key)))


def _write_qmc1(path: Path, payload: bytes) -> None:
    from echoshift.qmc import qmc1

    path.write_bytes(qmc1.decrypt(payload))


def _run_chunks_in_process(
    source: Path, destination: Path, info, chunk_bytes: int
) -> int:
    """Exactly what the pool would do, just without the pool."""
    key = None if info.format.is_qmc1 else parse_ekey(info.ekey)

    chunks = build_chunks(
        source=source,
        destination=destination,
        total=info.audio_length,
        key=key,
        is_qmc1=info.format.is_qmc1,
        chunk_bytes=chunk_bytes,
    )
    assert len(chunks) > 1, "test payload should split into several chunks"

    with open(destination, "wb") as handle:
        handle.truncate(info.audio_length)

    for index, chunk in enumerate(chunks):
        offset, head, error = run_chunk(chunk)
        assert error is None, error
        assert offset == chunk.offset
        if index == 0:
            assert head is not None and head.startswith(b"fLaC")
    return len(chunks)


@pytest.mark.parametrize(
    "kind,key", [("qmc1", None), ("map", MAP_KEY), ("rc4", RC4_KEY)]
)
def test_chunked_decryption_reproduces_the_serial_result(
    tmp_path: Path, kind: str, key: bytes | None
):
    """Chunk boundaries must not change a single output byte."""
    payload = _payload()
    source = tmp_path / ("song.qmcflac" if kind == "qmc1" else "song.mflac")
    if kind == "qmc1":
        _write_qmc1(source, payload)
    else:
        _write_qmc2(source, payload, key)

    info = inspect(source)
    serial = decoder_module.decrypt_to(info, tmp_path / "serial.flac", workers=1)
    assert serial.read_bytes() == payload

    chunked_path = tmp_path / "chunked.flac"
    count = _run_chunks_in_process(source, chunked_path, info, chunk_bytes=64 * 1024)
    assert count > 5
    assert chunked_path.read_bytes() == payload


def test_chunk_boundaries_are_segment_aligned_for_rc4(tmp_path: Path):
    """Misaligned RC4 chunks would still be correct, but would redo work."""
    source = tmp_path / "song.mflac"
    _write_qmc2(source, _payload(), RC4_KEY)
    info = inspect(source)

    chunks = build_chunks(
        source=source,
        destination=tmp_path / "out.flac",
        total=info.audio_length,
        key=parse_ekey(info.ekey),
        is_qmc1=False,
        chunk_bytes=256 * 1024,
    )
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert chunk.offset % OTHER_SEGMENT_SIZE == 0
        assert chunk.length % OTHER_SEGMENT_SIZE == 0


def test_qmc1_chunks_are_not_forced_to_rc4_alignment(tmp_path: Path):
    source = tmp_path / "song.qmcflac"
    _write_qmc1(source, _payload())
    info = inspect(source)
    chunks = build_chunks(
        source=source,
        destination=tmp_path / "out.flac",
        total=info.audio_length,
        key=None,
        is_qmc1=True,
        chunk_bytes=64 * 1024,
    )
    assert all(chunk.length == 64 * 1024 for chunk in chunks[:-1])


def test_run_chunk_reports_a_short_read(tmp_path: Path):
    from echoshift.qmc.parallel import _Chunk

    source = tmp_path / "truncated.bin"
    source.write_bytes(b"x" * 100)
    destination = tmp_path / "out.bin"
    destination.write_bytes(b"")

    chunk = _Chunk(
        source=str(source),
        destination=str(destination),
        key=None,
        is_qmc1=True,
        offset=0,
        length=500,
    )
    offset, head, error = run_chunk(chunk)
    assert offset == 0 and head is None
    assert error is not None and "短读" in error


# --------------------------------------------------------------------------- #
# the pool itself
# --------------------------------------------------------------------------- #


def test_pool_availability_is_reported_as_a_bool():
    assert isinstance(pool_available(), bool)


def test_parallel_path_reports_what_it_did(tmp_path, monkeypatch):
    """Either the pool really runs, or we get an explained fallback."""
    monkeypatch.setattr(parallel, "MIN_PARALLEL_BYTES", 4096)
    payload = _payload()
    source = tmp_path / "song.mflac"
    _write_qmc2(source, payload, RC4_KEY)
    info = inspect(source)

    destination = tmp_path / "out.flac"
    handled, error, probe_error = parallel.decrypt_parallel(
        source=source,
        destination=destination,
        total=info.audio_length,
        key=parse_ekey(info.ekey),
        is_qmc1=False,
        workers=4,
        chunk_bytes=64 * 1024,
    )
    assert probe_error is None

    if pool_available():
        assert handled is True, f"pool reported available but failed: {error}"
        assert destination.read_bytes() == payload
    else:
        assert handled is False
        assert error and "子进程" in error
        pytest.skip(f"此环境不允许创建进程池，已确认回退行为：{error}")


def test_decrypt_to_succeeds_either_way(tmp_path, monkeypatch):
    """Whichever path is taken, the bytes must come out right."""
    monkeypatch.setattr(parallel, "MIN_PARALLEL_BYTES", 4096)
    payload = _payload()
    source = tmp_path / "song.mflac"
    _write_qmc2(source, payload, MAP_KEY)

    warnings: list[str] = []
    result = decoder_module.decrypt_to(
        inspect(source),
        tmp_path / "out.flac",
        workers=4,
        chunk_size=8192,
        on_warning=warnings.append,
    )
    assert result.read_bytes() == payload
    if not pool_available():
        assert warnings and "并行解密不可用" in warnings[0]


def test_parallel_reports_progress_or_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, "MIN_PARALLEL_BYTES", 4096)
    payload = _payload()
    source = tmp_path / "song.mflac"
    _write_qmc2(source, payload, RC4_KEY)

    seen: list[float] = []
    out = decoder_module.decrypt_to(
        inspect(source),
        tmp_path / "out.flac",
        workers=4,
        chunk_size=8192,
        on_progress=seen.append,
    )
    assert seen and seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)
    assert out.read_bytes() == payload


def test_parallel_is_skipped_below_the_threshold(tmp_path: Path):
    """A small file must take the serial path rather than spin up a pool."""
    payload = _payload(20_000)
    source = tmp_path / "song.mflac"
    _write_qmc2(source, payload, RC4_KEY)

    result = decoder_module.decrypt_to(inspect(source), tmp_path / "out.flac", workers=4)
    assert result.read_bytes() == payload


def test_a_wrong_key_is_fatal_rather_than_retried(tmp_path, monkeypatch):
    """A bad ekey must surface immediately, not trigger a second serial pass."""
    monkeypatch.setattr(parallel, "MIN_PARALLEL_BYTES", 4096)
    payload = _payload()
    source = tmp_path / "evil.mflac"
    _write_qmc2(source, payload, RC4_KEY)

    blob = bytearray(source.read_bytes())
    wrong = bytes((i * 13 + 200) & 0xFF for i in range(512))
    source.write_bytes(bytes(blob[: len(blob) - 516]) + wrong + struct.pack("<I", 512))

    destination = tmp_path / "out.flac"
    with pytest.raises(DecryptionError) as excinfo:
        decoder_module.decrypt_to(
            inspect(source), destination, workers=4, chunk_size=8192
        )
    assert "ekey 很可能不正确" in str(excinfo.value)
    assert not destination.exists(), "a failed decryption must not leave a file behind"


def test_a_wrong_key_in_the_parallel_path_is_fatal_not_a_fallback(tmp_path, monkeypatch):
    """A rejected key must be reported as an error, not as "try serial".

    ``decrypt_parallel`` truncates the destination before the workers run, so a
    rejected key leaves that pre-created file behind unless the caller is told
    to clean up.  Reporting it as a fallback reason meant the caller decrypted
    the whole file again just to fail the same check, and its cleanup -- which
    owns the destination -- never ran.

    The file is deliberately left on disk: chunks already handed to the pool keep
    writing to it, so deleting it here would race those workers.  The scratch
    directory is rebuilt for every test.
    """
    monkeypatch.setattr(parallel, "MIN_PARALLEL_BYTES", 4096)
    if not pool_available():
        pytest.skip("此环境不允许创建进程池")

    payload = _payload()
    source = tmp_path / "evil.mflac"
    _write_qmc2(source, payload, RC4_KEY)
    blob = bytearray(source.read_bytes())
    wrong = bytes((i * 13 + 200) & 0xFF for i in range(512))
    source.write_bytes(bytes(blob[: len(blob) - 516]) + wrong + struct.pack("<I", 512))
    info = inspect(source)

    destination = tmp_path / "partial.flac"
    started = time.perf_counter()
    handled, error, probe_error = parallel.decrypt_parallel(
        source=source,
        destination=destination,
        total=info.audio_length,
        key=wrong,
        is_qmc1=False,
        workers=4,
        chunk_bytes=8192,
        head_probe=lambda head: decoder_module._verify_decrypted_head(info, head),
    )
    elapsed = time.perf_counter() - started

    assert handled is False
    assert error is None, f"错误的密钥不应被当作回退理由：{error}"
    assert isinstance(probe_error, DecryptionError)
    # Detected from the first chunk, not after decrypting the whole container.
    assert elapsed < 5.0, f"错误密钥应在首块就被发现，实际耗时 {elapsed:.1f}s"


def test_a_failed_serial_decryption_also_cleans_up(tmp_path: Path):
    payload = _payload(50_000)
    source = tmp_path / "evil.mflac"
    _write_qmc2(source, payload, RC4_KEY)

    blob = bytearray(source.read_bytes())
    wrong = bytes((i * 13 + 200) & 0xFF for i in range(512))
    source.write_bytes(bytes(blob[: len(blob) - 516]) + wrong + struct.pack("<I", 512))

    destination = tmp_path / "out.flac"
    with pytest.raises(DecryptionError):
        decoder_module.decrypt_to(inspect(source), destination, workers=1)
    assert not destination.exists()


def test_prepare_input_threads_workers_through(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, "MIN_PARALLEL_BYTES", 4096)
    payload = _payload()
    source = tmp_path / "song.mflac"
    _write_qmc2(source, payload, RC4_KEY)

    with prepare_input(
        source, tmp_path, keystore=KeyStore(), workers=4, chunk_size=8192
    ) as prepared:
        assert prepared.path.read_bytes() == payload


def test_raw_key_parsing_matches_the_embedded_key(tmp_path: Path):
    """The worker rebuilds the cipher from raw bytes, not the ekey string."""
    source = tmp_path / "song.mflac"
    _write_qmc2(source, _payload(5000), RC4_KEY)
    info = inspect(source)
    assert parse_ekey(info.ekey) == RC4_KEY


# --------------------------------------------------------------------------- #
# the pool is shared, and its size is bounded no matter how big the queue is
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not pool_available(), reason="此环境不允许创建进程池")
def test_the_pool_is_reused_across_containers(tmp_path, monkeypatch):
    """Process churn matters: one pool serves the whole batch.

    A pool per container used to spawn and tear down a set of interpreters for
    every file, which lands as a burst of process creation on top of the
    ffmpeg work already in flight.
    """
    from concurrent.futures import ProcessPoolExecutor

    monkeypatch.setattr(parallel, "MIN_PARALLEL_BYTES", 4096)
    parallel.reset_shared_pool()

    payload = _payload()
    sources = []
    for index in range(3):
        source = tmp_path / f"song{index}.mflac"
        _write_qmc2(source, payload, RC4_KEY)
        sources.append(source)

    created: list[ProcessPoolExecutor] = []
    real_pool = parallel.ProcessPoolExecutor

    def counting_pool(*args, **kwargs):
        pool = real_pool(*args, **kwargs)
        created.append(pool)
        return pool

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", counting_pool)
    try:
        for index, source in enumerate(sources):
            destination = tmp_path / f"out{index}.flac"
            handled, error, _probe_error = parallel.decrypt_parallel(
                source=source,
                destination=destination,
                total=inspect(source).audio_length,
                key=RC4_KEY,
                is_qmc1=False,
                workers=2,
                chunk_bytes=64 * 1024,
            )
            assert handled is True, error
            assert destination.read_bytes() == payload
    finally:
        parallel.reset_shared_pool()

    assert len(created) == 1, f"expected one shared pool, created {len(created)}"


def test_the_shared_pool_is_rebuilt_when_the_budget_changes(monkeypatch):
    """A batch with a different budget must not inherit a stale pool size."""
    if not pool_available():
        pytest.skip("此环境不允许创建进程池")
    from concurrent.futures import ProcessPoolExecutor

    from echoshift.qmc.parallel import _acquire_shared_pool

    created: list[ProcessPoolExecutor] = []
    real_pool = ProcessPoolExecutor

    def counting_pool(*args, **kwargs):
        pool = real_pool(*args, **kwargs)
        created.append(pool)
        return pool

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", counting_pool)
    parallel.reset_shared_pool()
    try:
        first = _acquire_shared_pool(2)
        again = _acquire_shared_pool(2)
        assert first is again, "the same budget must reuse the same pool"
        second = _acquire_shared_pool(3)
        assert second is not first, "a new budget needs a new pool"
    finally:
        parallel.reset_shared_pool()
    assert len(created) == 2
