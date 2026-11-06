import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.db import init_db, make_engine, session_factory
from app.models import Book, GenKind, Generation, GenStatus
from app.services.engine import EngineError, EngineUnavailable
from app.services.layout import PaperSize, PrintMode
from app.services.worker import (
    GenerationWorker,
    WorkerState,
    decode_request,
    encode_request,
)
from app.storage import Storage
from tests.fake_engine import FakeEngine


@pytest.fixture
def env(tmp_path):
    db = make_engine(tmp_path / "t.db")
    init_db(db)
    sessions = session_factory(db)
    with sessions() as s:
        s.add(Book(title="b", paper_size=PaperSize.A4, print_mode=PrintMode.HOME))
        s.commit()
    storage = Storage(tmp_path / "storage")
    engine = FakeEngine()
    worker = GenerationWorker(engine, sessions, storage, retry_backoff_s=0.05)
    return worker, engine, sessions, storage


def add_gen(sessions, request_json=None, status=GenStatus.QUEUED, **kw):
    rj = request_json or encode_request(
        kind=GenKind.CHARACTER, prompt="p", negative_prompt="n", seed=7, width=64, height=64
    )
    with sessions() as s:
        g = Generation(book_id=1, kind=GenKind.CHARACTER, user_text="u", prompt="p", negative_prompt="n",
                       seed=7, width=64, height=64, request_json=rj, status=status, **kw)
        s.add(g)
        s.commit()
        return g.id


def get(sessions, gid):
    with sessions() as s:
        return s.get(Generation, gid)


def test_encode_decode_roundtrip(tmp_path):
    storage = Storage(tmp_path)
    text = encode_request(kind=GenKind.PAGE, prompt="中文", negative_prompt="n", seed=3, width=10, height=20,
                          character_image="books/1/generations/1.png", structure_image="books/1/s.png",
                          structure_strength=0.4)
    assert json.loads(text)["kind"] == "page"
    req = decode_request(text, storage)
    assert req.kind == GenKind.PAGE and req.prompt == "中文" and req.seed == 3
    assert (req.width, req.height, req.structure_strength) == (10, 20, 0.4)
    assert req.character_image == (tmp_path / "books/1/generations/1.png").resolve()
    assert req.structure_image == (tmp_path / "books/1/s.png").resolve()


def test_decode_null_images_and_bad_input(tmp_path):
    storage = Storage(tmp_path)
    req = decode_request(encode_request(kind=GenKind.CHARACTER, prompt="p", negative_prompt="", seed=1,
                                        width=1, height=1), storage)
    assert req.character_image is None and req.structure_image is None
    with pytest.raises(ValueError):
        decode_request(encode_request(kind=GenKind.PAGE, prompt="p", negative_prompt="", seed=1, width=1,
                                      height=1, structure_image="../evil.png"), storage)
    with pytest.raises(ValueError):
        decode_request("not json", storage)


async def test_run_once_success_records_everything(env):
    worker, engine, sessions, storage = env
    engine.progress_steps = [(1, 4)]
    gid = add_gen(sessions)
    assert await worker.run_once() is True
    g = get(sessions, gid)
    assert g.status == GenStatus.SUCCEEDED
    assert g.image_path == "books/1/generations/1.png"
    assert storage.abs(g.image_path).read_bytes() == engine.image
    assert (g.engine, g.workflow_name, g.workflow_version, g.workflow_hash) == (
        "fake", "fake_wf", "abc123", "abc123def456")
    assert json.loads(g.models_json) == ["m1.safetensors", "m2.gguf"]
    assert json.loads(g.params_json)["標題"] == "中文"
    assert g.elapsed_s == 0.25 and g.error is None
    assert g.started_at <= g.finished_at
    assert engine.requests[0].seed == 7
    assert worker.progress == {} and worker.state == WorkerState.IDLE
    assert await worker.run_once() is False


async def test_progress_reported_during_job(env):
    worker, engine, sessions, _ = env
    gid = add_gen(sessions)
    seen = []
    orig = engine.generate

    async def spy(req, on_progress=None):
        on_progress(2, 4)
        seen.append(dict(worker.progress))
        return await orig(req, on_progress)

    engine.generate = spy
    await worker.run_once()
    assert seen == [{gid: (2, 4)}]


async def test_failure_marks_failed_and_continues(env):
    worker, engine, sessions, _ = env
    engine.outcomes.append(EngineError("boom"))
    a, b = add_gen(sessions), add_gen(sessions)
    await worker.run_once()
    await worker.run_once()
    ga, gb = get(sessions, a), get(sessions, b)
    assert ga.status == GenStatus.FAILED and "boom" in ga.error and ga.finished_at is not None
    assert gb.status == GenStatus.SUCCEEDED


async def test_unexpected_exception_marks_failed(env):
    worker, engine, sessions, _ = env
    engine.outcomes.append(RuntimeError("kaboom"))
    gid = add_gen(sessions)
    assert await worker.run_once() is True
    g = get(sessions, gid)
    assert g.status == GenStatus.FAILED and "kaboom" in g.error


async def test_bad_request_json_marks_failed(env):
    worker, engine, sessions, _ = env
    bad = add_gen(sessions, request_json="{oops")
    outside = add_gen(sessions, request_json=encode_request(
        kind=GenKind.PAGE, prompt="p", negative_prompt="", seed=1, width=1, height=1,
        structure_image="../../etc/passwd"))
    await worker.run_once()
    await worker.run_once()
    for gid in (bad, outside):
        g = get(sessions, gid)
        assert g.status == GenStatus.FAILED and g.error
    assert engine.requests == []


async def test_engine_unavailable_keeps_job_queued_and_retries(env):
    worker, engine, sessions, _ = env
    engine.error = EngineUnavailable("comfy down")
    gid = add_gen(sessions)
    await worker.run_once()
    g = get(sessions, gid)
    assert g.status == GenStatus.QUEUED and g.started_at is None
    assert worker.state == WorkerState.ENGINE_UNAVAILABLE and "comfy down" in worker.last_error
    engine.error = None
    await worker.run_once()
    assert get(sessions, gid).status == GenStatus.SUCCEEDED
    assert worker.state != WorkerState.ENGINE_UNAVAILABLE


async def test_cancelled_during_run_is_not_overwritten(env):
    worker, engine, sessions, _ = env
    gid = add_gen(sessions)
    orig = engine.generate

    async def cancel_then_generate(req, on_progress=None):
        with sessions() as s:
            g = s.get(Generation, gid)
            g.status = GenStatus.CANCELLED
            s.add(g)
            s.commit()
        return await orig(req, on_progress)

    engine.generate = cancel_then_generate
    await worker.run_once()
    assert get(sessions, gid).status == GenStatus.CANCELLED


def test_running_jobs_requeued_on_start(env):
    worker, _, sessions, _ = env
    gid = add_gen(sessions, status=GenStatus.RUNNING, started_at=datetime.now(timezone.utc))
    assert worker.requeue_interrupted() == 1
    g = get(sessions, gid)
    assert g.status == GenStatus.QUEUED and g.started_at is None
    assert worker.requeue_interrupted() == 0


async def test_fifo_and_queue_position(env):
    worker, engine, sessions, _ = env
    ids = [add_gen(sessions) for _ in range(3)]
    assert [worker.queue_position(i) for i in ids] == [0, 1, 2]
    await worker.run_once()
    assert engine.requests and get(sessions, ids[0]).status == GenStatus.SUCCEEDED
    assert worker.queue_position(ids[0]) is None
    assert [worker.queue_position(i) for i in ids[1:]] == [0, 1]
    await worker.run_once()
    assert get(sessions, ids[1]).status == GenStatus.SUCCEEDED
    assert get(sessions, ids[2]).status == GenStatus.QUEUED
    assert worker.queue_position(999) is None


def test_queue_position_counts_running_job(env):
    worker, _, sessions, _ = env
    running = add_gen(sessions, status=GenStatus.RUNNING)
    queued = add_gen(sessions)
    assert worker.queue_position(running) == 0
    assert worker.queue_position(queued) == 1


async def _wait_status(sessions, gid, status, timeout=2.0):
    async def poll():
        while get(sessions, gid).status != status:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def test_background_loop_processes_after_notify(env):
    worker, _, sessions, _ = env
    await worker.start()
    try:
        await asyncio.sleep(0.05)
        gid = add_gen(sessions)
        worker.notify()
        await _wait_status(sessions, gid, GenStatus.SUCCEEDED)
        assert worker.state == WorkerState.IDLE
    finally:
        await worker.stop()
    assert worker.state == WorkerState.STOPPED


async def test_notify_during_job_is_not_lost(env):
    worker, engine, sessions, _ = env
    engine.delay_s = 0.1
    first = add_gen(sessions)
    await worker.start()
    try:
        await _wait_status(sessions, first, GenStatus.RUNNING)
        second = add_gen(sessions)
        worker.notify()
        await _wait_status(sessions, second, GenStatus.SUCCEEDED, timeout=3)
    finally:
        await worker.stop()


async def test_loop_retries_after_backoff_and_survives_errors(env):
    worker, engine, sessions, _ = env
    engine.outcomes.extend([EngineUnavailable("down"), RuntimeError("bad")])
    a, b = add_gen(sessions), add_gen(sessions)
    await worker.start()
    try:
        await _wait_status(sessions, b, GenStatus.SUCCEEDED, timeout=3)
        assert get(sessions, a).status == GenStatus.FAILED  # 重試後吃到 RuntimeError
    finally:
        await worker.stop()


async def test_start_requeues_interrupted_and_stop_is_idempotent(env):
    worker, _, sessions, _ = env
    gid = add_gen(sessions, status=GenStatus.RUNNING, started_at=datetime.now(timezone.utc))
    await worker.start()
    try:
        await _wait_status(sessions, gid, GenStatus.SUCCEEDED)
    finally:
        await worker.stop()
        await worker.stop()
