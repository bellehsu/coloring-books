import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db import init_db, make_engine, session_factory
from app.models import Book, GenKind, Generation, GenStatus, Owner, Usage
from app.services.engine import EngineError, EngineUnavailable
from app.services.layout import PaperSize, PrintMode
from app.services.worker import (
    GenerationWorker,
    WorkerState,
    decode_request,
    encode_request,
)
from app.storage import LocalStorage, StorageNotFound
from tests.fake_engine import FakeEngine


def _owner(s) -> int:
    o = Owner(name="t", token_hash="h" * 64)
    s.add(o)
    s.commit()
    return o.id


@pytest.fixture
def env(tmp_path):
    db = make_engine(tmp_path / "t.db")
    init_db(db)
    sessions = session_factory(db)
    with sessions() as s:
        s.add(Book(title="b", paper_size=PaperSize.A4, print_mode=PrintMode.HOME, owner_id=_owner(s)))
        s.commit()
    storage = LocalStorage(tmp_path / "storage", b"test-signing-key")
    engine = FakeEngine()
    worker = GenerationWorker(engine, sessions, storage, retry_backoff_s=0.05)
    return worker, engine, sessions, storage


def add_gen(sessions, request_json=None, status=GenStatus.QUEUED, kind=GenKind.CHARACTER, pose_mode=None, **kw):
    rj = request_json or encode_request(
        kind=kind, prompt="p", negative_prompt="n", seed=7, width=64, height=64,
        extra={"pose_mode": pose_mode} if pose_mode is not None else None,
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
    storage = LocalStorage(tmp_path, b"test-signing-key")
    text = encode_request(kind=GenKind.POSE, prompt="中文", negative_prompt="", seed=3, width=10, height=20,
                          character_image="books/1/generations/1.png", variation="facing left")
    d = json.loads(text)
    assert d == {"kind": "pose", "prompt": "中文", "negative_prompt": "", "seed": 3, "width": 10, "height": 20,
                 "character_image": "books/1/generations/1.png", "variation": "facing left"}
    assert "中文" in text  # ensure_ascii=False
    storage.put("books/1/generations/1.png", b"char-png")
    req = decode_request(text, storage)
    assert req.kind == GenKind.POSE and req.prompt == "中文" and req.negative_prompt == "" and req.seed == 3
    assert (req.width, req.height, req.variation) == (10, 20, "facing left")
    assert req.character_image == b"char-png"  # 引擎收 bytes；request_json 仍記 key


def test_encode_request_records_variation_and_extra(tmp_path):
    # 變化語永遠寫（可為 null）；extra 併入 JSON 當作來源紀錄（pose_mode、background_style…）
    storage = LocalStorage(tmp_path, b"test-signing-key")
    d = json.loads(encode_request(kind=GenKind.CHARACTER, prompt="p", negative_prompt="", seed=1, width=8,
                                  height=8))
    assert "variation" in d and d["variation"] is None and d["character_image"] is None
    for gone in ("structure_image", "structure_strength", "pose_mode", "background_style"):
        assert gone not in d
    text = encode_request(kind=GenKind.BACKGROUND, prompt="p", negative_prompt="", seed=1, width=8, height=8,
                          variation="wide view", extra={"background_style": "simple"})
    d = json.loads(text)
    assert d["background_style"] == "simple" and d["variation"] == "wide view"
    req = decode_request(text, storage)
    assert req.kind == GenKind.BACKGROUND and req.variation == "wide view"
    d = json.loads(encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1, width=8, height=8,
                                  extra={"pose_mode": "precise"}))
    assert d["pose_mode"] == "precise"
    req = decode_request(json.dumps(d), storage)
    assert req.kind == GenKind.POSE and req.variation is None
    # extra 不能蓋掉核心欄位
    with pytest.raises(ValueError):
        encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1, width=8, height=8,
                       extra={"kind": "page"})


def test_decode_ignores_old_sdxl_keys_on_qwen_rows(tmp_path):
    # 舊列（Phase 8）的簡潔背景／精準動作還帶 structure_* 與 background_style_requested：照常還原
    storage = LocalStorage(tmp_path, b"test-signing-key")
    old = {"kind": "background", "prompt": "p", "negative_prompt": "", "seed": 1, "width": 8, "height": 8,
           "character_image": None, "structure_image": None, "structure_strength": 0.0,
           "background_style": "simple", "background_style_requested": "simple", "variation": "v"}
    req = decode_request(json.dumps(old), storage)
    assert req.kind == GenKind.BACKGROUND and req.variation == "v"
    old_pose = {"kind": "pose", "prompt": "p", "negative_prompt": "", "seed": 1, "width": 8, "height": 8,
                "character_image": "books/1/c.png", "structure_image": None, "structure_strength": 0.0,
                "pose_mode": "precise", "variation": None}
    storage.put("books/1/c.png", b"old-char")
    req = decode_request(json.dumps(old_pose), storage)
    assert req.kind == GenKind.POSE and req.character_image == b"old-char"


@pytest.mark.parametrize("retired", [{"pose_mode": "fast"}, {"background_style": "detailed"}, {"kind": "page"}])
def test_decode_request_rejects_retired(tmp_path, retired):
    storage = LocalStorage(tmp_path, b"test-signing-key")
    kind = {"pose_mode": GenKind.POSE, "background_style": GenKind.BACKGROUND}.get(next(iter(retired)),
                                                                                   GenKind.CHARACTER)
    d = json.loads(encode_request(kind=kind, prompt="p", negative_prompt="n", seed=1, width=8, height=8,
                                  character_image="books/1/c.png"))
    d.update(retired)
    with pytest.raises(ValueError, match="這種生成已停用"):
        decode_request(json.dumps(d), storage)


def test_decode_null_images_and_bad_input(tmp_path):
    storage = LocalStorage(tmp_path, b"test-signing-key")
    req = decode_request(encode_request(kind=GenKind.CHARACTER, prompt="p", negative_prompt="", seed=1,
                                        width=1, height=1), storage)
    assert req.character_image is None
    with pytest.raises(ValueError):
        decode_request(encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1, width=1,
                                      height=1, character_image="../evil.png"), storage)
    with pytest.raises(ValueError):
        decode_request("not json", storage)
    # key 合法但物件不存在：StorageNotFound（worker 回報「角色參考圖檔案遺失」）
    with pytest.raises(StorageNotFound):
        decode_request(encode_request(kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1, width=1,
                                      height=1, character_image="books/1/missing.png"), storage)


async def test_run_once_success_records_everything(env):
    worker, engine, sessions, storage = env
    engine.progress_steps = [(1, 4)]
    gid = add_gen(sessions)
    assert await worker.run_once() is True
    g = get(sessions, gid)
    assert g.status == GenStatus.SUCCEEDED
    assert g.image_path == "books/1/generations/1.png"
    assert storage.get(g.image_path) == engine.image
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
        kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1, width=1, height=1,
        character_image="../../etc/passwd"))
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


async def test_unavailable_requeue_cap(env):
    worker, engine, sessions, _ = env
    engine.error = EngineUnavailable("comfy down")
    gid = add_gen(sessions, request_json=encode_request(
        kind=GenKind.CHARACTER, prompt="p", negative_prompt="n", seed=7, width=64, height=64,
        extra={"usage_day": "2026-11-12", "usage_owner": 1}))
    with sessions() as s:
        s.add(Usage(owner_id=1, day=date(2026, 11, 12), generations=1))
        s.commit()
    for i in range(3):
        await worker.run_once()
        g = get(sessions, gid)
        assert g.status == GenStatus.QUEUED
        assert json.loads(g.request_json)["engine_requeues"] == i + 1
    await worker.run_once()
    g = get(sessions, gid)
    assert g.status == GenStatus.FAILED and g.error == "繪圖服務一直連不上，已停止重試"
    assert json.loads(g.request_json)["usage_refunded"] is True
    with sessions() as s:
        assert s.get(Usage, (1, date(2026, 11, 12))).generations == 0
    assert len(engine.requests) == 4


async def test_http_error_fails_job_once(env):
    worker, engine, sessions, _ = env
    engine.error = EngineError("GET /view 回傳 HTTP 404")
    gid = add_gen(sessions)
    await worker.run_once()
    g = get(sessions, gid)
    assert g.status == GenStatus.FAILED and "404" in g.error
    assert await worker.run_once() is False
    assert len(engine.requests) == 1


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


def test_worker_runs_oldest_first(env):
    worker, engine, sessions, _ = env
    t0 = datetime.now(timezone.utc)
    # 故意不依 id 順序建立：created_at 較早的先跑
    late = add_gen(sessions, created_at=t0 + timedelta(seconds=2))
    first = add_gen(sessions, created_at=t0)
    mid = add_gen(sessions, created_at=t0 + timedelta(seconds=1))
    assert [worker.queue_position(i) for i in (first, mid, late)] == [0, 1, 2]
    order = []
    for _ in range(3):
        gid = worker._claim().id
        order.append(gid)
        worker._finish(gid, status=GenStatus.SUCCEEDED)
    assert order == [first, mid, late]
    assert worker._claim() is None


def test_equal_created_at_breaks_tie_by_id(env):
    worker, _, sessions, _ = env
    t0 = datetime.now(timezone.utc)
    a = add_gen(sessions, created_at=t0)
    b = add_gen(sessions, created_at=t0)
    assert worker.queue_position(a) == 0 and worker.queue_position(b) == 1
    assert worker._claim().id == a


def test_running_job_counts_in_positions(env):
    worker, _, sessions, _ = env
    running = add_gen(sessions, status=GenStatus.RUNNING)
    queued = add_gen(sessions)
    assert worker.queue_position(queued) == 1
    assert worker.queue_position(running) == 0


def test_cancel_retired_on_start(env):
    worker, _, sessions, _ = env
    fast = add_gen(sessions, kind=GenKind.POSE, request_json=json.dumps({"kind": "pose", "pose_mode": "fast"}))
    page = add_gen(sessions, kind=GenKind.PAGE, request_json=json.dumps({"kind": "page"}))
    qwen = add_gen(sessions, kind=GenKind.POSE, pose_mode="precise")
    done = add_gen(sessions, kind=GenKind.PAGE, request_json=json.dumps({"kind": "page"}),
                   status=GenStatus.SUCCEEDED)
    assert worker.cancel_retired() == 2
    for gid in (fast, page):
        g = get(sessions, gid)
        assert g.status == GenStatus.CANCELLED and g.error == "這種生成已停用"
    assert get(sessions, qwen).status == GenStatus.QUEUED
    assert get(sessions, done).status == GenStatus.SUCCEEDED and get(sessions, done).error is None
    assert worker.cancel_retired() == 0


async def test_start_cancels_retired_queued_rows(env):
    worker, _, sessions, _ = env
    page = add_gen(sessions, kind=GenKind.PAGE, request_json=json.dumps({"kind": "page"}))
    await worker.start()
    try:
        assert get(sessions, page).status == GenStatus.CANCELLED
    finally:
        await worker.stop()


async def test_retired_row_in_run_once_has_bare_error(env):
    worker, engine, sessions, _ = env
    gid = add_gen(sessions, kind=GenKind.POSE, request_json=json.dumps({"kind": "pose", "pose_mode": "fast"}))
    assert await worker.run_once() is True
    g = get(sessions, gid)
    assert g.status == GenStatus.FAILED and g.error == "這種生成已停用"
    assert not engine.requests


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
    assert storage.get(g.mask_path).startswith(b"\x89PNG")


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
    out = np.asarray(Image.open(io.BytesIO(storage.get(g.mask_path))))
    assert int((out > 0).sum()) == 30 * 30


def _page(sessions, **kw):
    from app.models import Page

    with sessions() as s:
        p = Page(book_id=1, position=1, **kw)
        s.add(p)
        s.commit()
        return p.id


def _page_row(sessions, pid):
    from app.models import Page

    with sessions() as s:
        return s.get(Page, pid)


async def test_autoselect_fills_empty_slot(env):
    worker, engine, sessions, storage = env
    pid = _page(sessions)
    gid = add_gen(sessions, kind=GenKind.BACKGROUND, page_id=pid)
    assert await worker.run_once() is True
    assert _page_row(sessions, pid).background_generation_id == gid
    pose = add_gen(sessions, kind=GenKind.POSE, page_id=pid)
    assert await worker.run_once() is True
    assert _page_row(sessions, pid).pose_generation_id == pose


async def test_autoselect_skips_pose_without_mask(env):
    worker, engine, sessions, storage = env
    engine.pose_mask = False
    pid = _page(sessions)
    add_gen(sessions, kind=GenKind.POSE, page_id=pid)
    await worker.run_once()
    assert _page_row(sessions, pid).pose_generation_id is None


async def test_autoselect_never_replaces(env):
    worker, engine, sessions, storage = env
    pid = _page(sessions)
    first = add_gen(sessions, kind=GenKind.BACKGROUND, page_id=pid, status=GenStatus.SUCCEEDED,
                    image_path="books/1/generations/1.png")
    with sessions() as s:
        from app.models import Page

        pg = s.get(Page, pid)
        pg.background_generation_id = first
        s.add(pg)
        s.commit()
    add_gen(sessions, kind=GenKind.BACKGROUND, page_id=pid)
    assert await worker.run_once() is True
    assert _page_row(sessions, pid).background_generation_id == first


async def test_finish_write_retried_then_succeeds(env, monkeypatch):
    worker, engine, sessions, _ = env
    gid = add_gen(sessions)
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleeps.append(d)
        await real_sleep(0)

    monkeypatch.setattr("app.services.worker.asyncio.sleep", fake_sleep)
    fails = {"n": 2}

    # 只讓「收尾」的 commit 失敗：_claim 之後才開始計
    orig_finish = worker._finish

    def flaky_finish(*a, **kw):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise RuntimeError("database is locked")
        return orig_finish(*a, **kw)

    monkeypatch.setattr(worker, "_finish", flaky_finish)
    await worker.run_once()
    assert get(sessions, gid).status == GenStatus.SUCCEEDED
    assert sleeps == [0.2, 0.5]


async def test_finish_write_all_attempts_fail_then_retried_next_loop(env, monkeypatch):
    worker, engine, sessions, _ = env
    gid = add_gen(sessions)

    async def no_sleep(d):
        pass

    monkeypatch.setattr("app.services.worker.asyncio.sleep", no_sleep)
    orig_finish = worker._finish
    broken = {"on": True}

    def flaky_finish(*a, **kw):
        if broken["on"]:
            raise RuntimeError("database is locked")
        return orig_finish(*a, **kw)

    monkeypatch.setattr(worker, "_finish", flaky_finish)
    await worker.run_once()
    assert get(sessions, gid).status == GenStatus.RUNNING
    assert gid in worker._unfinished
    broken["on"] = False
    await worker._retry_unfinished()
    assert get(sessions, gid).status == GenStatus.SUCCEEDED
    assert not worker._unfinished


async def test_stop_interrupts_running_engine(env):
    worker, engine, sessions, _ = env
    engine.delay_s = 5
    add_gen(sessions)
    await worker.start()
    for _ in range(100):
        if worker.state == WorkerState.RUNNING:
            break
        await asyncio.sleep(0.01)
    await worker.stop()
    assert engine.interrupts == 1


async def test_stop_idle_does_not_interrupt(env):
    worker, engine, sessions, _ = env
    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()
    assert engine.interrupts == 0


class InterruptibleEngine(FakeEngine):
    """interrupt() 會讓執行中的 generate() 丟 EngineError（像 ComfyUI 回 execution_interrupted）。"""

    def __init__(self):
        super().__init__()
        self.stop_event = asyncio.Event()

    async def generate(self, req, on_progress=None):
        self.requests.append(req)
        await self.stop_event.wait()
        raise EngineError("執行被中斷")

    async def interrupt(self):
        await super().interrupt()
        self.stop_event.set()
        await asyncio.sleep(0.05)  # 真的 interrupt 有網路往返：worker 任務在這段時間會先看到錯誤


async def test_stop_interrupt_does_not_fail_running_job(env):
    worker, _, sessions, storage = env
    engine = InterruptibleEngine()
    worker.engine = engine
    gid = add_gen(sessions)
    await worker.start()
    for _ in range(200):
        if worker._in_engine:
            break
        await asyncio.sleep(0.01)
    await worker.stop()
    assert engine.interrupts == 1
    g = get(sessions, gid)
    assert g.status == GenStatus.RUNNING and g.error is None
    assert worker.requeue_interrupted() == 1
    assert get(sessions, gid).status == GenStatus.QUEUED


# ---- 撤銷：RUNNING 的讓它跑完，但不能再有這個 owner 的新工作開始（B9）----


def _revoke_owner(sessions, owner_id=1):
    from app.models import utcnow

    with sessions() as s:
        o = s.get(Owner, owner_id)
        o.revoked_at = utcnow()
        s.add(o)
        s.commit()


async def test_revoked_owner_requeued_job_is_cancelled_not_run(env):
    from app.services.usage import USAGE_DAY, USAGE_OWNER, taipei_day
    from app.models import utcnow

    worker, engine, sessions, _ = env
    day = taipei_day(utcnow())
    with sessions() as s:
        s.add(Usage(owner_id=1, day=day, generations=1))
        s.commit()
    base = json.loads(encode_request(kind=GenKind.CHARACTER, prompt="p", negative_prompt="n", seed=7, width=64, height=64))
    gid = add_gen(sessions, request_json=json.dumps({**base, USAGE_DAY: day.isoformat(), USAGE_OWNER: 1}))

    async def down_after_revoke(req, on_progress=None):  # 執行中被撤銷，之後引擎斷線 -> 放回佇列
        engine.requests.append(req)
        _revoke_owner(sessions)
        raise EngineUnavailable("comfy down")

    engine.generate = down_after_revoke
    await worker.run_once()
    assert get(sessions, gid).status == GenStatus.QUEUED  # 放回佇列
    assert len(engine.requests) == 1
    assert await worker.run_once() is False  # 下一輪：先取消撤銷者的排隊工作，不再跑引擎
    g = get(sessions, gid)
    assert g.status == GenStatus.CANCELLED and g.error == "邀請已撤銷"
    assert len(engine.requests) == 1
    with sessions() as s:
        assert s.get(Usage, (1, day)).generations == 0  # 額度退回


async def test_revoked_owner_success_followup_task_not_run(env, monkeypatch):
    from sqlmodel import select

    from app.models import PageTask, PageTaskStatus
    from app.services import pipeline

    worker, engine, sessions, _ = env
    pid = _page(sessions)
    real = engine.generate

    async def revoke_then_succeed(req, on_progress=None):
        _revoke_owner(sessions)
        return await real(req, on_progress)

    engine.generate = revoke_then_succeed
    add_gen(sessions, kind=GenKind.BACKGROUND, page_id=pid)
    assert await worker.run_once() is True  # 執行中的讓它跑完，並照常排了頁面流程
    with sessions() as s:
        task = s.exec(select(PageTask).where(PageTask.page_id == pid)).one()
        assert task.status == PageTaskStatus.QUEUED.value
    ran = []
    monkeypatch.setattr(pipeline, "run_pipeline", lambda *a, **k: ran.append(a), raising=False)
    assert await worker.run_page_tasks() == 0
    with sessions() as s:
        task = s.exec(select(PageTask).where(PageTask.page_id == pid)).one()
        assert task.status == PageTaskStatus.FAILED.value and task.error == "邀請已撤銷"
    assert ran == []
