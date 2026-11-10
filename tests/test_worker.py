import asyncio
import json
from datetime import datetime, timedelta, timezone

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


def add_gen(sessions, request_json=None, status=GenStatus.QUEUED, kind=GenKind.CHARACTER, pose_mode=None, **kw):
    rj = request_json or encode_request(
        kind=kind, prompt="p", negative_prompt="n", seed=7, width=64, height=64, pose_mode=pose_mode
    )
    with sessions() as s:
        g = Generation(book_id=1, kind=kind, user_text="u", prompt="p", negative_prompt="n",
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


def test_pose_mode_roundtrip_and_old_rows_are_fast(tmp_path):
    storage = Storage(tmp_path)
    text = encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1, width=8, height=8,
                          pose_mode="precise", variation="facing left")
    d = json.loads(text)
    assert d["pose_mode"] == "precise" and d["variation"] == "facing left"
    req = decode_request(text, storage)
    assert req.pose_mode == "precise" and req.variation == "facing left"
    precise_plain = json.loads(encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1,
                                              width=8, height=8, pose_mode="precise"))
    assert "variation" in precise_plain and precise_plain["variation"] is None
    # Phase 5 的舊列沒有 pose_mode → 快速
    old = encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="n", seed=1, width=8, height=8)
    assert "pose_mode" not in json.loads(old)
    req = decode_request(old, storage)
    assert req.pose_mode == "fast" and req.variation is None


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


def _qwen(sessions, **kw):
    return add_gen(sessions, kind=GenKind.POSE, pose_mode="precise", **kw)


def _sdxl(sessions, **kw):
    return add_gen(sessions, kind=GenKind.BACKGROUND, **kw)


def test_claim_order_matches_queue_position(env):
    worker, _, sessions, _ = env
    t0 = datetime.now(timezone.utc)
    ids = [
        _sdxl(sessions, created_at=t0),
        _qwen(sessions, created_at=t0 + timedelta(seconds=1)),
        _sdxl(sessions, created_at=t0 + timedelta(seconds=2)),
        _qwen(sessions, created_at=t0 + timedelta(seconds=3)),
        add_gen(sessions, created_at=t0 + timedelta(seconds=4)),  # CHARACTER → sdxl
    ]
    worker.last_family = "qwen"
    positions = {i: worker.queue_position(i) for i in ids}
    assert sorted(positions.values()) == [0, 1, 2, 3, 4]
    predicted = sorted(ids, key=positions.__getitem__)
    assert predicted == [ids[1], ids[3], ids[0], ids[2], ids[4]]

    claimed = []
    remaining = list(ids)
    while remaining:
        # 沒有任務執行中 → 位置 0 的那筆必須是下一個被取走的
        head = [i for i in remaining if worker.queue_position(i) == 0]
        gen = worker._claim()
        assert gen is not None and [gen.id] == head
        claimed.append(gen.id)
        remaining.remove(gen.id)
        assert worker.queue_position(gen.id) == 0
        # 執行中 → 其餘依序為 1..n，且位置 1 就是下一個被取走的
        assert sorted(worker.queue_position(i) for i in remaining) == list(range(1, len(remaining) + 1))
        worker._finish(gen.id, status=GenStatus.SUCCEEDED, finished_at=datetime.now(timezone.utc))
    assert claimed == predicted
    assert worker._claim() is None


def test_running_job_positions_follow_running_family(env):
    worker, _, sessions, _ = env
    t0 = datetime.now(timezone.utc)
    q1 = _qwen(sessions, created_at=t0)
    b2 = _sdxl(sessions, created_at=t0 + timedelta(seconds=1))
    q3 = _qwen(sessions, created_at=t0 + timedelta(seconds=2))
    assert worker._claim().id == q1
    assert worker.last_family == "qwen"
    assert worker.queue_position(q3) == 1 and worker.queue_position(b2) == 2
    worker._finish(q1, status=GenStatus.SUCCEEDED)
    assert worker._claim().id == q3


def test_overdue_job_claimed_first(env):
    worker, _, sessions, _ = env
    now = datetime.now(timezone.utc)
    old_bg = _sdxl(sessions, created_at=now - timedelta(seconds=200))
    q = _qwen(sessions, created_at=now - timedelta(seconds=5))
    worker.last_family = "qwen"
    assert worker.queue_position(old_bg) == 0 and worker.queue_position(q) == 1
    assert worker._claim().id == old_bg


def test_position_stable_while_running_and_claim_uses_plan_clock(env, monkeypatch):
    """執行中「逾時」以上次取任務的時間判斷：顯示的第 1 位就是下一個被取走的。"""
    worker, _, sessions, _ = env
    t0 = datetime.now(timezone.utc)
    clock = [t0]
    monkeypatch.setattr("app.services.worker.utcnow", lambda: clock[0])
    p1 = _qwen(sessions, created_at=t0 - timedelta(seconds=110))
    b = _sdxl(sessions, created_at=t0 - timedelta(seconds=100))
    p2 = _qwen(sessions, created_at=t0 - timedelta(seconds=10))
    assert worker._claim().id == p1
    assert worker.plan_clock == t0
    assert worker.queue_position(p2) == 1 and worker.queue_position(b) == 2
    # P1 仍在執行，時間超過 max_wait_s：B 以現在時間看已逾時，但顯示不變
    clock[0] = t0 + timedelta(seconds=100)
    assert worker.queue_position(p2) == 1 and worker.queue_position(b) == 2
    p3 = _qwen(sessions, created_at=t0 + timedelta(seconds=50))
    assert [worker.queue_position(i) for i in (p2, p3, b)] == [1, 2, 3]
    worker._finish(p1, status=GenStatus.SUCCEEDED)
    assert worker.queue_position(p2) == 0  # 兩個任務之間也與下一次取任務一致
    assert worker._claim().id == p2
    assert worker.plan_clock == t0 + timedelta(seconds=100)
    # 以新的 plan_clock 判斷：B 逾時，排在同家族的 P3 前面
    assert worker.queue_position(b) == 1 and worker.queue_position(p3) == 2
    worker._finish(p2, status=GenStatus.SUCCEEDED)
    assert worker._claim().id == b


def test_idle_uses_current_time_and_resets_plan_clock(env, monkeypatch):
    worker, _, sessions, _ = env
    t0 = datetime.now(timezone.utc)
    clock = [t0]
    monkeypatch.setattr("app.services.worker.utcnow", lambda: clock[0])
    assert worker.plan_clock is None
    q = _qwen(sessions, created_at=t0)
    assert worker._claim().id == q and worker.plan_clock == t0
    worker._finish(q, status=GenStatus.SUCCEEDED)
    assert worker._claim() is None
    assert worker.plan_clock is None  # 佇列空 → 閒置，之後以現在時間判斷
    clock[0] = t0 + timedelta(seconds=500)
    b = _sdxl(sessions, created_at=t0 + timedelta(seconds=10))  # 已等 490 秒
    q2 = _qwen(sessions, created_at=t0 + timedelta(seconds=400))
    assert worker.queue_position(b) == 0 and worker.queue_position(q2) == 1
    assert worker._claim().id == b
    assert worker.plan_clock == t0 + timedelta(seconds=500)


def test_last_family_updates_on_claim(env):
    worker, _, sessions, _ = env
    assert worker.last_family is None
    q = _qwen(sessions)
    assert worker._claim().id == q
    assert worker.last_family == "qwen"
    worker._finish(q, status=GenStatus.SUCCEEDED)
    b = _sdxl(sessions)
    assert worker._claim().id == b
    assert worker.last_family == "sdxl"
    worker._finish(b, status=GenStatus.SUCCEEDED)
    assert worker._claim() is None
    assert worker.last_family == "sdxl"  # 佇列空時不改變


def test_restart_requeued_running_job_sorts_oldest_first(env):
    worker, _, sessions, _ = env
    t0 = datetime.now(timezone.utc)
    newer_ids = [_sdxl(sessions, created_at=t0 + timedelta(seconds=1)),
                 _qwen(sessions, created_at=t0 + timedelta(seconds=2))]
    interrupted = _qwen(sessions, status=GenStatus.RUNNING, started_at=t0, created_at=t0)
    assert worker.last_family is None  # 重啟後不知道上一個家族
    assert worker.requeue_interrupted() == 1
    assert [worker.queue_position(i) for i in [interrupted, *newer_ids]] == [0, 1, 2]
    assert worker._claim().id == interrupted


def test_max_wait_s_passed_to_worker(env):
    worker, engine, sessions, storage = env
    assert worker.max_wait_s == 180.0
    w = GenerationWorker(engine, sessions, storage, retry_backoff_s=0.05, max_wait_s=0)
    t0 = datetime.now(timezone.utc)
    b = _sdxl(sessions, created_at=t0 - timedelta(seconds=2))
    _qwen(sessions, created_at=t0 - timedelta(seconds=1))
    w.last_family = "qwen"
    assert w._claim().id == b  # max_wait_s=0 → 任何等待都逾時 → 最舊優先


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


async def test_notify_between_empty_poll_and_wait_is_not_lost(env):
    worker, _, sessions, _ = env
    real = worker.run_once
    calls = []
    added = []

    async def racy():
        if not calls:
            calls.append(1)
            result = await real()  # 空佇列，回 False
            added.append(add_gen(sessions))  # 之後才有新任務到達並 notify
            worker.notify()
            return result
        return await real()

    worker.run_once = racy
    await worker.start()
    try:
        async def until_added():
            while not added:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(until_added(), 2)
        await _wait_status(sessions, added[0], GenStatus.SUCCEEDED)
    finally:
        await worker.stop()


async def test_returns_to_idle_when_queue_empties_after_outage(env):
    worker, engine, sessions, _ = env
    worker.retry_backoff_s = 0.2
    engine.error = EngineUnavailable("down")
    gid = add_gen(sessions)
    calls = 0
    real = worker.run_once

    async def counting():
        nonlocal calls
        calls += 1
        return await real()

    worker.run_once = counting
    await worker.start()
    try:
        async def until_unavailable():
            while worker.state != WorkerState.ENGINE_UNAVAILABLE:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(until_unavailable(), 2)
        with sessions() as s:
            g = s.get(Generation, gid)
            g.status = GenStatus.CANCELLED
            s.add(g)
            s.commit()
        engine.error = None
        await asyncio.sleep(0.5)  # 下一次重試後佇列為空
        assert worker.state == WorkerState.IDLE and worker.last_error is None
        settled = calls
        await asyncio.sleep(0.6)  # 閒置時不可再輪詢
        assert calls == settled
    finally:
        await worker.stop()


async def test_notify_from_other_thread_wakes_loop(env):
    worker, _, sessions, _ = env
    await worker.start()
    try:
        await asyncio.sleep(0.05)
        gid = add_gen(sessions)
        await asyncio.to_thread(worker.notify)
        await _wait_status(sessions, gid, GenStatus.SUCCEEDED)
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


async def test_pose_mask_saved_and_path_set(env):
    worker, engine, sessions, storage = env
    rj = encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="n", seed=7, width=64, height=64)
    gid = add_gen(sessions, request_json=rj)
    await worker.run_once()
    g = get(sessions, gid)
    assert g.status == GenStatus.SUCCEEDED
    assert g.mask_path == storage.generation_mask(1, gid)
    assert storage.abs(g.mask_path).read_bytes().startswith(b"\x89PNG")


async def test_non_pose_has_no_mask_path(env):
    worker, engine, sessions, storage = env
    gid = add_gen(sessions)
    await worker.run_once()
    assert get(sessions, gid).mask_path is None


async def test_worker_stores_largest_component_mask_for_pose(env):
    import io

    import numpy as np
    from PIL import Image

    worker, engine, sessions, storage = env
    a = np.zeros((64, 64), np.uint8)
    a[5:35, 5:35] = 255
    a[55:60, 40:60] = 255
    buf = io.BytesIO()
    Image.fromarray(a, "L").save(buf, "PNG")
    engine.mask_override = buf.getvalue()
    rj = encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="n", seed=7, width=64, height=64)
    gid = add_gen(sessions, request_json=rj)
    await worker.run_once()
    g = get(sessions, gid)
    out = np.asarray(Image.open(storage.abs(g.mask_path)))
    assert int((out > 0).sum()) == 30 * 30
