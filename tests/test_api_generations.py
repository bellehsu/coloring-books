import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.services.engine import EngineUnavailable
from tests.fake_engine import FakeEngine
from tests.test_api_books import candidates, new_book, run_jobs
from tests.test_api_lineart import color_png, seed_generation, set_page
from tests.test_api_pages import book_with_character, new_page


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def app(settings_file, engine):
    return create_app(load_settings(settings_file), engine=engine, start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def test_generation_detail_progress_and_image(client, app):
    b = new_book(client)
    g = candidates(client, b["id"], seed=1, count=1).json()[0]
    d = client.get(f"/api/generations/{g['id']}").json()
    assert d["status"] == "queued" and d["queue_position"] == 0 and d["image_url"] is None
    assert client.get(f"/api/generations/{g['id']}/image").status_code == 404
    run_jobs(client, app)
    d = client.get(f"/api/generations/{g['id']}").json()
    assert d["status"] == "succeeded" and d["image_url"] == f"/api/generations/{g['id']}/image"
    r = client.get(d["image_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
    assert client.get("/api/generations/999").status_code == 404
    assert client.get("/api/generations/999/image").status_code == 404


def test_discard_and_list_filter(client, app):
    b, char_id = book_with_character(client, app)
    page = new_page(client, b["id"])
    a, c = (seed_generation(app, b["id"], page["id"], color_png()) for _ in range(2))
    set_page(app, page["id"], selected_generation_id=a)
    base = f"/api/books/{b['id']}/generations"
    ids = [g["id"] for g in client.get(base).json()]
    assert ids == sorted(ids, reverse=True) and {a, c, char_id} <= set(ids)
    assert [g["id"] for g in client.get(base, params={"page_id": page["id"]}).json()] == [c, a]
    assert [g["id"] for g in client.get(base, params={"kind": "character"}).json()] == [char_id]
    assert client.get(base, params={"kind": "bogus"}).status_code == 422
    assert client.get("/api/books/999/generations").status_code == 404

    assert client.patch(f"/api/generations/{a}", json={"discarded": True}).status_code == 409
    assert client.patch(f"/api/generations/{char_id}", json={"discarded": True}).status_code == 409
    assert client.patch("/api/generations/999", json={"discarded": True}).status_code == 404
    r = client.patch(f"/api/generations/{c}", json={"discarded": True})
    assert r.status_code == 200 and r.json()["discarded"] is True
    assert c not in [g["id"] for g in client.get(base).json()]
    assert c in [g["id"] for g in client.get(base, params={"include_discarded": True}).json()]
    r = client.patch(f"/api/generations/{c}", json={"discarded": False})
    assert r.status_code == 200 and r.json()["discarded"] is False
    assert c in [g["id"] for g in client.get(base).json()]


def _discarded_at(app, gen_id):
    from app.models import Generation

    with app.state.sessions() as s:
        return s.get(Generation, gen_id).discarded_at


def test_discard_sets_and_restore_clears_discarded_at(client, app, monkeypatch):
    # 保留期限（storage_gc 30 天）從 discarded_at 起算：捨棄時記下，還原時清掉；重複捨棄不延後期限
    from datetime import datetime, timezone

    from app.api import generations as gen_api

    b, _ = book_with_character(client, app)
    page = new_page(client, b["id"])
    c = seed_generation(app, b["id"], page["id"], color_png())
    assert _discarded_at(app, c) is None
    t1 = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gen_api, "utcnow", lambda: t1)
    assert client.patch(f"/api/generations/{c}", json={"discarded": True}).status_code == 200
    assert _discarded_at(app, c) == t1
    monkeypatch.setattr(gen_api, "utcnow", lambda: datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert client.patch(f"/api/generations/{c}", json={"discarded": True}).status_code == 200
    assert _discarded_at(app, c) == t1  # 已捨棄的再捨棄一次：期限不重算
    r = client.patch(f"/api/generations/{c}", json={"discarded": False})
    assert r.status_code == 200 and r.json()["discarded"] is False
    assert _discarded_at(app, c) is None


def test_discard_blocked_leaves_discarded_at_null(client, app):
    b, char_id = book_with_character(client, app)
    assert client.patch(f"/api/generations/{char_id}", json={"discarded": True}).status_code == 409
    assert _discarded_at(app, char_id) is None


def test_restore_refused_after_gc_cleared_image(client, app):
    from app.models import Generation

    b, _ = book_with_character(client, app)
    page = new_page(client, b["id"])
    c = seed_generation(app, b["id"], page["id"], color_png())
    assert client.patch(f"/api/generations/{c}", json={"discarded": True}).status_code == 200
    with app.state.sessions() as s:  # storage_gc 清掉了過期捨棄圖
        g = s.get(Generation, c)
        g.image_path = None
        s.add(g)
        s.commit()
    r = client.patch(f"/api/generations/{c}", json={"discarded": False})
    assert r.status_code == 409 and r.json()["detail"] == "這張圖已被清理，無法還原"
    d = client.get(f"/api/generations/{c}").json()
    assert d["discarded"] is True and _discarded_at(app, c) is not None
    # 捨棄本身照常（冪等）
    assert client.patch(f"/api/generations/{c}", json={"discarded": True}).status_code == 200


def test_cancel_only_queued(client, app):
    b = new_book(client)
    (g1,) = candidates(client, b["id"], seed=1).json()  # 一次一張
    (g2,) = candidates(client, b["id"], seed=2).json()
    r = client.post(f"/api/generations/{g1['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(f"/api/generations/{g1['id']}/cancel").status_code == 409
    run_jobs(client, app)  # 只剩 g2
    assert not client.portal.call(app.state.worker.run_once)
    assert client.get(f"/api/generations/{g1['id']}").json()["status"] == "cancelled"
    assert client.post(f"/api/generations/{g2['id']}/cancel").status_code == 409
    assert client.post("/api/generations/999/cancel").status_code == 404


def test_queue_status_reports_engine_unavailable(client, app, engine):
    q = client.get("/api/system/queue").json()
    assert q == {
        "state": "stopped", "last_error": None, "queued": 0,
        "pending": [], "recent": [],
    }
    b = new_book(client)
    candidates(client, b["id"], seed=1)
    candidates(client, b["id"], seed=2)
    assert client.get("/api/system/queue").json()["queued"] == 2
    engine.error = EngineUnavailable("ComfyUI 連不上")
    client.portal.call(app.state.worker.run_once)
    q = client.get("/api/system/queue").json()
    assert q["state"] == "engine_unavailable" and q["last_error"]
    assert q["queued"] == 2


def test_discard_queued_also_cancels(client, app, engine):
    b = new_book(client)
    g = candidates(client, b["id"], seed=1, count=1).json()[0]
    r = client.patch(f"/api/generations/{g['id']}", json={"discarded": True})
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled" and r.json()["discarded"] is True
    assert not client.portal.call(app.state.worker.run_once)
    assert engine.requests == []


def _add_gens(app, rows):
    """rows：(kind, status, elapsed) 或 (kind, status, elapsed, engine, workflow_name)。
    沒給 engine／workflow 時當成目前的 ComfyUI Qwen 生成。"""
    from datetime import timedelta

    from app.models import Generation, GenKind, GenStatus, utcnow
    from app.services.comfy_engine import WORKFLOWS

    t0 = utcnow()
    with app.state.sessions() as s:
        for i, row in enumerate(rows):
            kind, status, elapsed = row[:3]
            engine, workflow = row[3:] if len(row) > 3 else (
                "comfyui", WORKFLOWS.get(GenKind(kind)))
            s.add(Generation(
                book_id=1, kind=GenKind(kind), status=GenStatus(status), user_text="u", prompt="p",
                negative_prompt="n", seed=1, width=8, height=8, request_json="{}", elapsed_s=elapsed,
                engine=engine, workflow_name=workflow, created_at=t0 + timedelta(seconds=i),
            ))
        s.commit()


def test_queue_status_pending_lists_kinds_oldest_first(client, app):
    new_book(client)
    _add_gens(app, [
        ("pose", "running", None), ("character", "queued", None), ("composite", "queued", None),
        ("page", "queued", None), ("background", "queued", None), ("pose", "failed", None),
        ("background", "cancelled", None),
    ])
    q = client.get("/api/system/queue").json()
    assert q["pending"] == [{"kind": "pose"}, {"kind": "character"}, {"kind": "background"}]


def test_queue_status_recent_filters_orders_and_caps_per_kind(client, app):
    new_book(client)
    rows = [("pose", "succeeded", 100.0 + i) for i in range(12)]  # 最舊 100 … 最新 111
    rows += [("background", "succeeded", 5.0), ("background", "succeeded", None),
             ("background", "failed", 9.0), ("composite", "succeeded", 1.0),
             ("page", "succeeded", 1.0), ("character", "queued", 3.0)]
    _add_gens(app, rows)
    recent = client.get("/api/system/queue").json()["recent"]
    pose = [r["elapsed_s"] for r in recent if r["kind"] == "pose"]
    assert pose == [111.0 - i for i in range(10)]
    assert [r for r in recent if r["kind"] == "background"] == [{"kind": "background", "elapsed_s": 5.0}]
    assert {r["kind"] for r in recent} == {"pose", "background"}


def test_queue_status_recent_only_current_comfy_workflows(client, app):
    """估時只看目前的 Qwen workflow：上傳的背景（耗時約 0）與舊 SDXL 列不算。"""
    new_book(client)
    _add_gens(app, [
        ("background", "succeeded", 60.0),
        ("background", "succeeded", 0.0, "upload", None),
        ("background", "succeeded", 15.0, "comfyui", "sdxl_background"),
        ("pose", "succeeded", 12.0, "comfyui", "sdxl_pose_cutout"),
        ("pose", "succeeded", 90.0),
        ("character", "succeeded", 3.0, "fake", None),
    ])
    recent = client.get("/api/system/queue").json()["recent"]
    assert recent == [{"kind": "background", "elapsed_s": 60.0}, {"kind": "pose", "elapsed_s": 90.0}]


def _act_as_non_admin(app):
    """同一位 owner，但不是 admin（dependency override，不動資料庫）。"""
    from sqlmodel import select

    from app.api.deps import current_owner
    from app.models import Owner

    with app.state.sessions() as s:
        o = s.exec(select(Owner)).first()
        fake = Owner(id=o.id, name=o.name, token_hash=o.token_hash, is_admin=False)
    app.dependency_overrides[current_owner] = lambda: fake


@pytest.mark.parametrize("raw", [
    "ComfyUI 執行失敗：FileNotFoundError: /ai-data/models/unet/qwen.gguf",
    "無法讀取 /home/belle/ComfyUI/output/x.png",
    "OSError: /tmp/comfy/abc.png 不存在",
    "找不到 /opt/comfy/custom_nodes/gguf",
    r"ComfyUI 執行失敗：C:\ComfyUI\models\unet\qwen.gguf 不存在",
])
def test_public_engine_error_hides_paths_from_non_admin(raw):
    from app.services.engine import public_engine_error

    assert public_engine_error(raw, False) == "繪圖時發生錯誤，請再試一次"
    assert public_engine_error(raw, True) == raw  # 管理者看原文


def test_public_engine_error_keeps_plain_text_and_hides_urls():
    from app.services.engine import public_engine_error

    assert public_engine_error("ComfyUI 執行失敗：記憶體不足", False) == "ComfyUI 執行失敗：記憶體不足"
    assert public_engine_error("GET http://127.0.0.1:8188/view 失敗", False) == "繪圖服務暫時無法連線，稍後會自動重試"
    assert public_engine_error(None, False) is None


def test_engine_error_text_hidden_from_non_admin(client, app, engine):
    raw = "ComfyUI 無法連線：無法連線 ComfyUI（http://127.0.0.1:8188）：All connection attempts failed"
    fixed = "繪圖服務暫時無法連線，稍後會自動重試"
    b = new_book(client)
    g = candidates(client, b["id"], seed=1, count=1).json()[0]
    engine.error = EngineUnavailable(raw)
    client.portal.call(app.state.worker.run_once)
    assert "127.0.0.1:8188" in client.get("/api/system/queue").json()["last_error"]  # admin：原文
    # 一筆含位址的失敗錯誤
    from app.models import Generation, GenStatus

    with app.state.sessions() as s:
        row = s.get(Generation, g["id"])
        row.status, row.error = GenStatus.FAILED, "GET http://127.0.0.1:8188/view 失敗"
        s.add(row)
        s.commit()
    assert "127.0.0.1:8188" in client.get(f"/api/generations/{g['id']}").json()["error"]
    _act_as_non_admin(app)
    q = client.get("/api/system/queue").json()
    assert q["last_error"] == fixed
    assert client.get(f"/api/generations/{g['id']}").json()["error"] == fixed
    assert "127.0.0.1" not in client.get(f"/api/books/{b['id']}/generations").text
