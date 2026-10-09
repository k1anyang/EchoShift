"""Regression tests for bounded conversion scheduling."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from echoshift.core.pipeline import (
    JobResult,
    JobState,
    Pipeline,
    PipelineOptions,
    decrypt_process_budget,
    effective_workers,
)
from echoshift.core.settings import EncodeSettings


def test_effective_workers_caps_cpu_oversubscription() -> None:
    assert effective_workers(16, cpu_count=2) == 1
    assert effective_workers(16, cpu_count=4) == 2
    assert effective_workers(16, cpu_count=32) == 4
    assert effective_workers(0, cpu_count=8) == 1


def test_run_batch_keeps_only_bounded_workers_active(tmp_path: Path) -> None:
    pipeline = Pipeline(None, PipelineOptions(settings=EncodeSettings()))  # type: ignore[arg-type]
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_run_job(
        source: Path,
        *,
        index: int,
        on_progress=None,
        cancel=None,
        decrypt_budget=None,
    ) -> JobResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        return JobResult(source=source, state=JobState.DONE)

    pipeline.run_job = fake_run_job  # type: ignore[method-assign]
    sources = [tmp_path / f"{index}.flac" for index in range(30)]
    results = pipeline.run_batch(sources, workers=16)

    assert len(results) == len(sources)
    assert all(result.state is JobState.DONE for result in results)
    assert peak <= effective_workers(16)


# --------------------------------------------------------------------------- #
# decryption process budget
#
# Decryption is pure Python, so a batch that decrypts inside several worker
# threads at once holds the GIL in turn: the CPU stays mostly idle, the batch
# takes several times longer, and Tk's event loop is starved until the window
# stops responding.  Two runner designs caused exactly that, and these tests
# pin both.
# --------------------------------------------------------------------------- #


def test_decrypt_budget_is_bounded_regardless_of_queue_size() -> None:
    """Adding files must never add decryption processes."""
    assert decrypt_process_budget(4, cpu_count=64) == 4
    assert decrypt_process_budget(4, cpu_count=8) == 4
    assert decrypt_process_budget(2, cpu_count=64) == 4
    # A small machine still gets a working budget, just a small one.
    assert decrypt_process_budget(4, cpu_count=2) == 1


def test_decrypt_budget_never_forces_thread_only_decryption() -> None:
    """The budget must stay above one whenever more than one file runs.

    A budget of one is what the old per-batch override produced, and it sent
    every decryption back into a worker thread.
    """
    for cpu_count in (2, 4, 8, 20, 64):
        for workers in (1, 2, 4):
            budget = decrypt_process_budget(workers, cpu_count=cpu_count)
            assert budget >= 1
            if workers > 1 and cpu_count >= 4:
                assert budget > 1, (
                    f"workers={workers} cpu={cpu_count} would decrypt in threads"
                )


def test_run_batch_hands_a_process_budget_to_every_job(tmp_path: Path) -> None:
    """The budget has to survive the trip from run_batch to run_job."""
    pipeline = Pipeline(None, PipelineOptions(settings=EncodeSettings()))  # type: ignore[arg-type]
    seen: list[int | None] = []

    def fake_run_job(
        source: Path,
        *,
        index: int,
        on_progress=None,
        cancel=None,
        decrypt_budget=None,
    ) -> JobResult:
        seen.append(decrypt_budget)
        return JobResult(source=source, state=JobState.DONE)

    pipeline.run_job = fake_run_job  # type: ignore[method-assign]
    sources = [tmp_path / f"{index}.flac" for index in range(4)]
    pipeline.run_batch(sources, workers=4)

    assert seen and all(value is not None for value in seen)
    assert set(seen) == {decrypt_process_budget(4)}


def test_the_operator_setting_is_never_widened(tmp_path: Path) -> None:
    """An explicit single-process decryption request wins over the budget."""
    options = PipelineOptions(settings=EncodeSettings(), decrypt_workers=1)
    pipeline = Pipeline(None, options)  # type: ignore[arg-type]
    captured: list[int] = []

    def fake_prepare(source, work_dir, **kwargs):
        captured.append(int(kwargs["workers"]))
        raise RuntimeError("stop here: the worker count is all we need")

    import echoshift.core.pipeline as pipeline_module

    original = pipeline_module.prepare_input
    pipeline_module.prepare_input = fake_prepare  # type: ignore[assignment]
    try:
        source = tmp_path / "song.mflac"
        source.write_bytes(b"x")
        pipeline.run_job(source, decrypt_budget=4)
    finally:
        pipeline_module.prepare_input = original

    assert captured == [1]


# --------------------------------------------------------------------------- #
# job numbering
#
# Converting a subset (retry failed, convert selected) used to number the jobs
# from one, so a three-file retry logged "[1]"-"[3]" for queue rows 17, 42 and
# 91 and sent anyone reading the log to the wrong file.
# --------------------------------------------------------------------------- #


def _pipeline_recording_indices(seen: list[int]) -> Pipeline:
    pipeline = Pipeline(None, PipelineOptions(settings=EncodeSettings()))  # type: ignore[arg-type]

    def fake_run_job(
        source: Path,
        *,
        index: int,
        on_progress=None,
        cancel=None,
        decrypt_budget=None,
    ) -> JobResult:
        seen.append(index)
        return JobResult(source=source, state=JobState.DONE)

    pipeline.run_job = fake_run_job  # type: ignore[method-assign]
    return pipeline


def test_run_batch_numbers_jobs_from_one_by_default(tmp_path: Path) -> None:
    seen: list[int] = []
    pipeline = _pipeline_recording_indices(seen)
    pipeline.run_batch([tmp_path / f"{i}.flac" for i in range(3)], workers=1)
    assert seen == [1, 2, 3]


def test_run_batch_uses_the_caller_supplied_queue_numbers(tmp_path: Path) -> None:
    seen: list[int] = []
    pipeline = _pipeline_recording_indices(seen)
    sources = [tmp_path / f"{i}.flac" for i in range(3)]
    pipeline.run_batch(sources, workers=2, index_labels=[17, 42, 91])
    assert sorted(seen) == [17, 42, 91]


def test_a_mismatched_label_list_falls_back_instead_of_mislabelling(tmp_path: Path) -> None:
    """Two labels for three sources must not silently drop the third."""
    seen: list[int] = []
    pipeline = _pipeline_recording_indices(seen)
    sources = [tmp_path / f"{i}.flac" for i in range(3)]
    pipeline.run_batch(sources, workers=1, index_labels=[17, 42])
    assert seen == [1, 2, 3]
