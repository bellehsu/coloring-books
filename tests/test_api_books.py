import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import Generation, GenKind, GenStatus
from app.services.prompts import CHARACTER_NEGATIVE, character_prompt
from app.services.worker import encode_request
from tests.fake_engine import FakeEngine


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def run_jobs(client, app, n=1):
    for _ in range(n):
        assert client.portal.call(app.state.worker.run_once)


def new_book(client, **kw):
    body = {"title": "小熊", "paper_size": "a4", "print_mode": "print_shop", **kw}
    r = client.post("/api/books", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def candidates(client, book_id, **kw):
    return client.post(f"/api/books/{book_id}/character/candidates",
                       json={"description": "一隻戴帽子的小熊", **kw})


def test_create_and_get_book_with_layout(client):
    b = new_book(client)
    assert b["layout"]["generation_size"] == [864, 1216]
    assert b["layout"]["content_px"] == [2551, 3579]
    assert client.get(f"/api/books/{b['id']}").json() == b
    assert client.post("/api/books", json={"title": "x", "paper_size": "A0",
                                           "print_mode": "home"}).status_code == 422
    assert client.post("/api/books", json={"title": "", "paper_size": "a4",
                                           "print_mode": "home"}).status_code == 422


def test_list_patch_and_404(client):
    a, b = new_book(client, title="一"), new_book(client, title="二")
    assert [x["id"] for x in client.get("/api/books").json()] == [b["id"], a["id"]]
    r = client.patch(f"/api/books/{a['id']}", json={"title": "新名"})
    assert r.status_code == 200 and r.json()["title"] == "新名"
    assert r.json()["updated_at"] >= a["updated_at"]
    assert client.get("/api/books/999").status_code == 404
    assert client.patch("/api/books/999", json={"title": "x"}).status_code == 404
    assert candidates(client, 999).status_code == 404
    assert client.post("/api/books/999/character", json={"generation_id": 1}).status_code == 404


def test_character_candidates_queue_four(client):
    b = new_book(client)
    r = candidates(client, b["id"], seed=100)
    assert r.status_code == 202
    gens = r.json()
    assert len(gens) == 4
    assert [g["seed"] for g in gens] == [100, 101, 102, 103]
    assert {g["status"] for g in gens} == {"queued"}
    assert {g["kind"] for g in gens} == {"character"}
    assert {(g["width"], g["height"]) for g in gens} == {(832, 1216)}
    assert all("一隻戴帽子的小熊" in g["prompt"] for g in gens)
    assert [g["queue_position"] for g in gens] == [0, 1, 2, 3]
    assert all(g["image_url"] is None and g["progress"] is None for g in gens)


def test_candidates_stored_fields_and_random_seed(client, app):
    b = new_book(client)
    gens = candidates(client, b["id"], count=2).json()
    assert gens[1]["seed"] == gens[0]["seed"] + 1
    with app.state.sessions() as s:
        g = s.get(Generation, gens[0]["id"])
        assert g.user_text == "一隻戴帽子的小熊"
        assert g.prompt == character_prompt("一隻戴帽子的小熊")
        assert g.negative_prompt == CHARACTER_NEGATIVE
        assert g.request_json == encode_request(
            kind=GenKind.CHARACTER, prompt=g.prompt, negative_prompt=CHARACTER_NEGATIVE,
            seed=g.seed, width=832, height=1216)


def test_candidates_validate_prompt_length(client, app):
    b = new_book(client)
    r = client.post(f"/api/books/{b['id']}/character/candidates", json={"description": "熊" * 801})
    assert r.status_code == 422
    with app.state.sessions() as s:
        assert s.exec(select(Generation)).all() == []
    r = client.post(f"/api/books/{b['id']}/character/candidates", json={"description": "熊" * 800})
    assert r.status_code == 202
    assert len(r.json()[0]["prompt"]) < 2000
    for bad in ({"count": 0}, {"count": 9}):
        assert candidates(client, b["id"], **bad).status_code == 422


def test_set_character_and_lock(client, app):
    b = new_book(client)
    gens = candidates(client, b["id"], seed=1, count=2).json()
    run_jobs(client, app)
    done = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert done.status_code == 200
    out = done.json()
    assert out["character_generation_id"] == gens[0]["id"]
    assert out["character_description"] == "一隻戴帽子的小熊"
    assert out["updated_at"] >= b["updated_at"]
    assert client.get(f"/api/books/{b['id']}").json()["character_generation_id"] == gens[0]["id"]
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert r.status_code == 409
    assert candidates(client, b["id"]).status_code == 409


def test_set_character_rejects_invalid(client, app):
    b = new_book(client)
    other = new_book(client, title="別本")
    mine = candidates(client, b["id"], count=3).json()
    theirs = candidates(client, other["id"], count=1).json()
    run_jobs(client, app, 4)
    with app.state.sessions() as s:
        s.get(Generation, mine[0]["id"]).status = GenStatus.FAILED
        s.get(Generation, mine[1]["id"]).discarded = True
        page = Generation(book_id=b["id"], kind=GenKind.PAGE, status=GenStatus.SUCCEEDED,
                          user_text="x", prompt="x", negative_prompt="", seed=1, width=8,
                          height=8, request_json="{}")
        s.add(page)
        s.commit()
        page_id = page.id
    for gid in (mine[0]["id"], mine[1]["id"], theirs[0]["id"], page_id):
        r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid})
        assert r.status_code == 409, gid
        assert r.json()["detail"]
    assert client.post(f"/api/books/{b['id']}/character", json={"generation_id": 9999}).status_code == 404
    assert client.get(f"/api/books/{b['id']}").json()["character_generation_id"] is None
