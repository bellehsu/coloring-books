import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.services.engine import EngineUnavailable
from tests.fake_engine import FakeEngine
from tests.test_api_books import candidates, new_book, run_jobs
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


def page_candidates(client, page_id, **kw):
    r = client.post(f"/api/pages/{page_id}/candidates", json=kw)
    assert r.status_code == 202, r.text
    return r.json()


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
    gens = page_candidates(client, page["id"], count=2, seed=5)
    run_jobs(client, app, 2)
    a, c = gens[0]["id"], gens[1]["id"]
    assert client.post(f"/api/pages/{page['id']}/select", json={"generation_id": a}).status_code == 200
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


def test_cancel_only_queued(client, app):
    b = new_book(client)
    g1, g2 = candidates(client, b["id"], seed=1, count=2).json()
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
    assert q == {"state": "stopped", "last_error": None, "queued": 0, "running_id": None}
    b = new_book(client)
    candidates(client, b["id"], seed=1, count=2)
    assert client.get("/api/system/queue").json()["queued"] == 2
    engine.error = EngineUnavailable("ComfyUI 連不上")
    client.portal.call(app.state.worker.run_once)
    q = client.get("/api/system/queue").json()
    assert q["state"] == "engine_unavailable" and q["last_error"]
    assert q["queued"] == 2 and q["running_id"] is None
