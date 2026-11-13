"""刪除繪本（fix round 2026-11-13 #1）：資料列、物件、排隊中的工作、權限、分享連結。"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, func, select

from app.config import load_settings
from app.main import create_app
from app.models import Generation, LineartEdit, Page, PageHistory, PageTask, PageTaskStatus, StrokeLayer, Usage
from app.services.book_delete import delete_books_rows
from tests.fake_engine import FakeEngine
from tests.test_api_books import new_book
from tests.test_api_compose import gen_background, gen_pose
from tests.test_pipeline import book_page, drain
from tests.test_sharing import as_, a_book, share, world  # noqa: F401  (world fixture)


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _ids(app, book_id) -> tuple[list[int], list[int]]:
    with app.state.sessions() as s:
        pages = list(s.exec(select(Page.id).where(col(Page.book_id) == book_id)).all())
        gens = list(s.exec(select(Generation.id).where(col(Generation.book_id) == book_id)).all())
    return pages, gens


def _counts(app, book_id, page_ids=None, gen_ids=None) -> dict:
    """各表的列數。page_ids／gen_ids 要在刪除前先取成清單傳進來：刪除之後再用子查詢找會永遠是空的（空轉）。"""
    if page_ids is None:
        page_ids, gen_ids = _ids(app, book_id)
    with app.state.sessions() as s:
        n = lambda q: s.exec(q).one()  # noqa: E731
        return {
            "pages": n(select(func.count()).select_from(Page).where(col(Page.book_id) == book_id)),
            "gens": n(select(func.count()).select_from(Generation).where(col(Generation.book_id) == book_id)),
            "tasks": n(select(func.count()).select_from(PageTask).where(col(PageTask.page_id).in_(page_ids))),
            "history": n(select(func.count()).select_from(PageHistory).where(col(PageHistory.page_id).in_(page_ids))),
            "edits": n(select(func.count()).select_from(LineartEdit).where(
                col(LineartEdit.page_id).in_(page_ids) | col(LineartEdit.generation_id).in_(gen_ids))),
        }


def _fill(app, book_id, page_id, gen_id) -> None:
    """確保 PageTask、PageHistory、LineartEdit 都有列（edit 同時指到這頁與這筆生成紀錄）。"""
    with app.state.sessions() as s:
        s.add(PageTask(page_id=page_id, status=PageTaskStatus.SUCCEEDED.value))
        s.add(PageHistory(page_id=page_id, seq=999, snapshot_json="{}"))
        if s.get(LineartEdit, (gen_id, StrokeLayer.USER)) is None:
            s.add(LineartEdit(generation_id=gen_id, layer=StrokeLayer.USER, page_id=page_id))
        s.commit()


def test_delete_book_removes_rows_and_storage(client, app):
    b, p = book_page(client, app)
    drain(client, app)
    st = app.state.storage
    assert list(st.list(f"books/{b['id']}/"))
    page_ids, gen_ids = _ids(app, b["id"])
    _fill(app, b["id"], page_ids[0], gen_ids[0])
    before = _counts(app, b["id"], page_ids, gen_ids)
    assert all(v > 0 for v in before.values()), before  # 每個表都有列，刪除才有意義
    assert client.delete(f"/api/books/{b['id']}").status_code == 204
    assert client.get(f"/api/books/{b['id']}").status_code == 404
    assert set(_counts(app, b["id"], page_ids, gen_ids).values()) == {0}
    assert list(st.list(f"books/{b['id']}/")) == []


def test_delete_book_cancels_and_refunds_queued(client, app):
    b, p = book_page(client, app)
    drain(client, app)

    def used():
        with app.state.sessions() as s:
            return sum(u.generations for u in s.exec(select(Usage)).all())

    gen_pose(client, app, p["id"], run=False)  # 排隊、不跑
    before = used()
    assert client.delete(f"/api/books/{b['id']}").status_code == 204
    assert used() == before - 1


def test_delete_book_leaves_other_books_untouched(client, app):
    b1, p1 = book_page(client, app)
    drain(client, app)
    b2, p2 = book_page(client, app)
    drain(client, app)
    c2, keys2 = _counts(app, b2["id"]), set(app.state.storage.list(f"books/{b2['id']}/"))
    assert keys2
    assert client.delete(f"/api/books/{b1['id']}").status_code == 204
    assert _counts(app, b2["id"]) == c2
    assert set(app.state.storage.list(f"books/{b2['id']}/")) == keys2
    assert client.get(f"/api/books/{b2['id']}").status_code == 200


def test_admin_cannot_delete_another_owners_book(world):  # noqa: F811
    app, client, sids, ids = world
    b, _ = a_book(client, app, sids["a"])
    before = _counts(app, b["id"])
    as_(client, sids["admin"])
    r = client.delete(f"/api/books/{b['id']}")
    assert r.status_code == 404 and r.json()["detail"] == "找不到這本繪本"
    assert _counts(app, b["id"]) == before
    as_(client, sids["a"])
    assert client.get(f"/api/books/{b['id']}").status_code == 200


def test_delete_book_while_generation_running(client, app, monkeypatch):
    b, p = book_page(client, app)
    drain(client, app)
    gen_pose(client, app, p["id"], run=False)
    before = set(app.state.storage.list(f"books/{b['id']}/"))
    engine = app.state.engine
    real = engine.generate

    async def deleting(*a, **kw):
        def go():
            with app.state.sessions() as s:
                delete_books_rows(s, [b["id"]])
                s.commit()
        await asyncio.to_thread(go)
        return await real(*a, **kw)

    monkeypatch.setattr(engine, "generate", deleting)
    assert client.portal.call(app.state.worker.run_once) is True
    # 列不見了：worker 寫的圖與遮罩都被清掉，沒有新的孤兒物件
    assert set(app.state.storage.list(f"books/{b['id']}/")) == before


def test_share_link_404_after_delete(world):  # noqa: F811
    app, client, sids, ids = world
    b, _ = a_book(client, app, sids["a"])
    assert share(client, b["id"]).status_code == 200
    as_(client, sids["b"])
    assert client.get(f"/api/shared/books/{b['id']}").status_code == 200
    as_(client, sids["a"])
    assert client.delete(f"/api/books/{b['id']}").status_code == 204
    as_(client, sids["b"])
    assert client.get(f"/api/shared/books/{b['id']}").status_code == 404


def test_book_ids_never_reused(client):
    a = new_book(client)["id"]
    assert client.delete(f"/api/books/{a}").status_code == 204
    b = new_book(client)["id"]
    assert b > a


def test_book_out_mine(world):  # noqa: F811
    app, client, sids, ids = world
    mb, _ = a_book(client, app, sids["a"])
    ab, _ = a_book(client, app, sids["admin"])
    as_(client, sids["admin"])
    mine = {b["id"]: b["mine"] for b in client.get("/api/books").json()}
    assert mine == {mb["id"]: False, ab["id"]: True}
    assert client.get(f"/api/books/{mb['id']}").json()["mine"] is False
    as_(client, sids["a"])
    assert client.get(f"/api/books/{mb['id']}").json()["mine"] is True


def test_delete_prefix_failure_still_204(client, app, monkeypatch):
    b, p = book_page(client, app)
    drain(client, app)
    page_ids, gen_ids = _ids(app, b["id"])

    def boom(prefix):
        raise OSError("storage down")

    monkeypatch.setattr(app.state.storage, "delete_prefix", boom)
    assert client.delete(f"/api/books/{b['id']}").status_code == 204
    assert set(_counts(app, b["id"], page_ids, gen_ids).values()) == {0}


def test_delete_book_while_page_task_running(client, app, monkeypatch):
    from app.services import pipeline

    b, p = book_page(client, app)
    gen_background(client, app, p["id"])
    gen_pose(client, app, p["id"])
    drain(client, app)  # 背景與角色動作都有了，流程跑完
    page_ids, gen_ids = _ids(app, b["id"])
    with app.state.sessions() as s:  # 頁面流程要重跑：排一個新的 task
        pipeline.enqueue_page_task(s, p["id"])
        s.commit()
    real = pipeline.compose_page
    called = []

    def deleting(session, storage, page_id):
        called.append(1)
        with app.state.sessions() as s2:
            delete_books_rows(s2, [b["id"]])
            s2.commit()
        return real(session, storage, page_id)

    monkeypatch.setattr(pipeline, "compose_page", deleting)
    drain(client, app)  # 不可丟例外
    assert called
    c = _counts(app, b["id"], page_ids, gen_ids)
    assert set(c.values()) == {0}, c  # 沒有重新長出列


def test_delete_book_rows_under_batch_lock(client, app, monkeypatch):
    """刪列時拿著 BATCH_LOCK：不會和「繼續做」／批次排隊交錯（排到一半的書被刪掉）。"""
    from app.api import books as books_api
    from app.services.jobs import BATCH_LOCK

    b, _ = book_page(client, app)
    drain(client, app)
    seen = []
    real = books_api.delete_books_rows

    def spy(session, ids):
        seen.append(BATCH_LOCK.locked())
        return real(session, ids)

    monkeypatch.setattr(books_api, "delete_books_rows", spy)
    assert client.delete(f"/api/books/{b['id']}").status_code == 204
    assert seen == [True]
    assert not BATCH_LOCK.locked()
