"""Storage I/O 不可在 SQLite 寫入交易進行中（final review B-I2）。

R2 的 put 是網路請求（逾時加重試可達數十秒）；若在寫入交易裡做，其他寫入者（別的使用者、worker 的 _finish、
session 的 last_seen）會等到 busy timeout 後拿到 database is locked。上傳背景與確認合成改成：
先以 RUNNING（沒有圖）提交生成紀錄 → 交易外寫物件 → 第二個短交易標成 SUCCEEDED 並記上 key。
失敗時刪掉暫存列（列永遠不會指到不存在的物件；留下的物件由 storage_gc 清）。
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import select, text

from app.config import load_settings
from app.db import SQLITE_BUSY_TIMEOUT_S
from app.main import create_app
from app.models import Generation, GenKind, GenStatus
from tests.fake_engine import FakeEngine
from tests.test_api_books import new_book
from tests.test_api_compose import compose_ready, composites, png_bytes, upload_bg
from tests.test_api_lineart import A5_HOME, get_page
from tests.test_api_pages import new_page

GATE_TIMEOUT_S = 10


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class GatedStorage:
    """生成圖的 put 停在閘門前（模擬很慢的 R2 PUT），直到測試放行；可設定放行後丟例外。"""

    def __init__(self, inner):
        self.inner = inner
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail: Exception | None = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def put(self, key, data, content_type="image/png"):
        if "/generations/" in key:
            self.entered.set()
            assert self.release.wait(GATE_TIMEOUT_S), "測試沒有放行"
            if self.fail is not None:
                raise self.fail
        return self.inner.put(key, data, content_type)


def gate(app) -> GatedStorage:
    g = GatedStorage(app.state.storage)
    app.state.storage = g
    return g


def db_writable(app) -> bool:
    """另一個連線能不能立刻拿到寫入鎖（timeout 很短：被鎖住就是 False，不會等 30 秒）。"""
    con = sqlite3.connect(app.state.settings.paths.database, timeout=0.2)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def in_thread(fn):
    out: dict = {}

    def run():
        out["r"] = fn()

    th = threading.Thread(target=run)
    th.start()
    return th, out


def assert_others_can_write_while_put_blocked(client, app, g: GatedStorage):
    assert g.entered.wait(GATE_TIMEOUT_S), "請求沒有走到 storage.put"
    assert db_writable(app), "storage.put 進行中仍持有 SQLite 寫入鎖"
    t0 = time.monotonic()
    r = client.post("/api/books", json={"title": "同時建立", "paper_size": "a4", "print_mode": "home"})
    assert r.status_code == 201, r.text
    assert time.monotonic() - t0 < 3  # 沒有等 busy timeout
    # 暫存的上傳／合成列不是 GPU 任務：佇列狀態不顯示它
    q = client.get("/api/system/queue").json()
    assert q["pending"] == []


def test_busy_timeout_is_raised(app, client):
    assert SQLITE_BUSY_TIMEOUT_S >= 30
    with app.state.sessions() as s:
        assert s.exec(text("PRAGMA busy_timeout")).one()[0] == SQLITE_BUSY_TIMEOUT_S * 1000


def test_upload_background_put_does_not_block_other_writers(client, app):
    b = new_book(client, **A5_HOME)
    p = new_page(client, b["id"])
    g = gate(app)
    th, out = in_thread(lambda: upload_bg(client, p["id"], png_bytes()))
    try:
        assert_others_can_write_while_put_blocked(client, app, g)
    finally:
        g.release.set()
        th.join(GATE_TIMEOUT_S)
    r = out["r"]
    assert r.status_code == 201, r.text
    gid = r.json()["background_generation_id"]
    with app.state.sessions() as s:
        gen = s.get(Generation, gid)
    assert gen.status == GenStatus.SUCCEEDED and gen.engine == "upload"
    assert gen.image_path and app.state.storage.inner.exists(gen.image_path)
    assert gen.finished_at is not None


def test_compose_put_does_not_block_other_writers(client, app):
    b, page, bg, pose = compose_ready(client, app)
    g = gate(app)
    th, out = in_thread(lambda: client.post(f"/api/pages/{page['id']}/compose"))
    try:
        assert_others_can_write_while_put_blocked(client, app, g)
    finally:
        g.release.set()
        th.join(GATE_TIMEOUT_S)
    r = out["r"]
    assert r.status_code == 200, r.text
    sel = r.json()["selected_generation_id"]
    [comp] = composites(app, page["id"])
    assert comp.id == sel and comp.status == GenStatus.SUCCEEDED
    assert comp.image_path and app.state.storage.inner.exists(comp.image_path)


def _bg_rows(app, page_id):
    with app.state.sessions() as s:
        return list(s.exec(select(Generation).where(
            Generation.page_id == page_id, Generation.kind == GenKind.BACKGROUND)).all())


def _objects(storage):
    return set(storage.list("books/"))


def test_upload_put_failure_leaves_no_row(client, app):
    b = new_book(client, **A5_HOME)
    p = new_page(client, b["id"])
    g = gate(app)
    g.fail = OSError("R2 逾時")
    g.release.set()
    before = _objects(g.inner)
    r = upload_bg(client, p["id"], png_bytes())
    assert r.status_code == 500
    assert _bg_rows(app, p["id"]) == []  # 暫存列已刪
    assert _objects(g.inner) == before
    assert client.post("/api/books", json={"title": "x", "paper_size": "a4", "print_mode": "home"}).status_code == 201


def test_compose_put_failure_leaves_no_row(client, app):
    b, page, bg, pose = compose_ready(client, app)
    g = gate(app)
    g.fail = OSError("R2 逾時")
    g.release.set()
    r = client.post(f"/api/pages/{page['id']}/compose")
    assert r.status_code == 500
    assert composites(app, page["id"]) == []
    assert get_page(app, page["id"]).selected_generation_id is None


def test_upload_pick_conflict_keeps_object_of_committed_row(client, app, monkeypatch):
    """背景列已經成功提交（有圖）之後選用失敗：列留著（可之後再選），物件不可被刪。"""
    import app.api.compose as compose_api

    b = new_book(client, **A5_HOME)
    p = new_page(client, b["id"])

    def conflict(*a, **kw):
        raise HTTPException(409, "這張背景已無法選用（可能剛被捨棄）")

    monkeypatch.setattr(compose_api, "_pick", conflict)
    r = upload_bg(client, p["id"], png_bytes())
    assert r.status_code == 409
    [row] = _bg_rows(app, p["id"])
    assert row.status == GenStatus.SUCCEEDED and row.image_path
    assert app.state.storage.exists(row.image_path)


def _insert(app, book_id, **kw) -> int:
    with app.state.sessions() as s:
        gen = Generation(book_id=book_id, kind=GenKind.BACKGROUND, user_text="", prompt="", negative_prompt="",
                         seed=0, width=8, height=8, request_json="{}", **kw)
        s.add(gen)
        s.commit()
        return gen.id


def test_restart_fails_interrupted_local_rows_instead_of_queueing_them(client, app):
    """上傳／合成寫到一半時伺服器停掉：重啟後那一列標成失敗，不可變成 QUEUED 被 worker 拿去生成。"""
    b = new_book(client, **A5_HOME)
    upload = _insert(app, b["id"], status=GenStatus.RUNNING, engine="upload")
    comp = _insert(app, b["id"], status=GenStatus.RUNNING, engine="compose")
    job = _insert(app, b["id"], status=GenStatus.RUNNING)
    app.state.worker.requeue_interrupted()
    with app.state.sessions() as s:
        rows = {i: s.get(Generation, i) for i in (upload, comp, job)}
    assert rows[job].status == GenStatus.QUEUED
    for i in (upload, comp):
        assert rows[i].status == GenStatus.FAILED and rows[i].image_path is None
        assert rows[i].error and rows[i].finished_at is not None
