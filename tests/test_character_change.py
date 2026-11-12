"""Phase 13 Task 4：上傳自己的主角（POST /books/{id}/character/upload）與確認後換角色（confirm_change）。"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.books import CHARACTER_SIZE
from app.config import load_settings
from app.main import create_app
from app.models import Book, Generation, GenKind, GenStatus
from tests.fake_engine import FakeEngine
from tests.test_api_books import candidate_ids, new_book, run_jobs
from tests.test_api_compose import gen_pose, png_bytes
from tests.test_api_lineart import A5_HOME, color_png, get_page
from tests.test_api_pages import book_with_character, new_page


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def upload_char(client, book_id, data, name="me.png", **form):
    return client.post(f"/api/books/{book_id}/character/upload",
                       files={"file": (name, data, "image/png")}, data=form)


def gen_row(app, gid) -> Generation:
    with app.state.sessions() as s:
        return s.get(Generation, gid)


def book_row(app, bid) -> Book:
    with app.state.sessions() as s:
        return s.get(Book, bid)


def stored_image(app, gen: Generation) -> Image.Image:
    with Image.open(io.BytesIO(app.state.storage.get(gen.image_path))) as im:
        return im.convert("RGB").copy()


def test_upload_character_letterboxes_on_white_and_selects_it(client, app):
    b = new_book(client)
    r = upload_char(client, b["id"], png_bytes(size=(2000, 900), color="red"))
    assert r.status_code == 201, r.text
    gid = r.json()["character_generation_id"]
    g = gen_row(app, gid)
    assert (g.kind, g.status, g.engine, g.page_id) == (GenKind.CHARACTER, GenStatus.SUCCEEDED, "upload", None)
    assert json.loads(g.request_json)["uploaded"] is True
    assert (g.width, g.height) == CHARACTER_SIZE
    params = json.loads(g.params_json)
    assert params["filename"] == "me.png" and params["original_size"] == [2000, 900]
    im = stored_image(app, g)
    assert im.size == CHARACTER_SIZE
    w, h = im.size
    assert im.getpixel((w // 2, 5)) == (255, 255, 255)  # 上下留白（不裁切、不拉伸）
    assert im.getpixel((w // 2, h - 5)) == (255, 255, 255)
    r_, g_, b_ = im.getpixel((w // 2, h // 2))
    assert r_ > 200 and g_ < 50 and b_ < 50
    # 寬圖縮到剛好等寬：圖的高度 ≈ 832 × 900 / 2000
    reds = [y for y in range(h) if im.getpixel((w // 2, y))[1] < 128]
    assert abs(len(reds) - round(w * 900 / 2000)) <= 2
    assert book_row(app, b["id"]).character_generation_id == gid
    # 角色動作可以用上傳的主角當參考圖
    p = new_page(client, b["id"], scene_prompt="公園")
    assert client.post(f"/api/pages/{p['id']}/poses", json={"action": "揮手"}).status_code == 202


def test_upload_character_validation(client, app):
    b = new_book(client)
    assert upload_char(client, b["id"], b"not an image").status_code == 400
    assert upload_char(client, b["id"], png_bytes(size=(40, 40))).status_code == 400  # 太小
    from app.services.uploads import MAX_UPLOAD_BYTES

    assert upload_char(client, b["id"], b"\0" * (MAX_UPLOAD_BYTES + 1)).status_code == 400
    buf = io.BytesIO()
    Image.new("1", (9000, 8000)).save(buf, "PNG")  # 7200 萬像素：超過上限
    r = upload_char(client, b["id"], buf.getvalue())
    assert r.status_code == 422
    assert book_row(app, b["id"]).character_generation_id is None
    with app.state.sessions() as s:
        from sqlmodel import select

        assert s.exec(select(Generation).where(Generation.book_id == b["id"])).all() == []


def test_upload_character_replaces_only_with_confirmation(client, app):
    app.state.engine.image = color_png()
    b, old = book_with_character(client, app, **A5_HOME)
    r = upload_char(client, b["id"], png_bytes())
    assert r.status_code == 409
    assert book_row(app, b["id"]).character_generation_id == old
    r = upload_char(client, b["id"], png_bytes(), confirm_change="true")
    assert r.status_code == 201, r.text
    assert r.json()["character_generation_id"] not in (None, old)


def test_change_character_needs_confirmation_and_keeps_poses(client, app):
    app.state.engine.image = color_png()
    b, old = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"], scene_prompt="公園")
    pose = gen_pose(client, app, p["id"])
    assert get_page(app, p["id"]).pose_generation_id == pose

    # 角色已設定：候選圖與選角色都要確認
    r = client.post(f"/api/books/{b['id']}/character/candidates", json={"description": "小狗"})
    assert r.status_code == 409
    [new] = candidate_ids(client, b["id"], 1, description="小狗", confirm_change=True)
    run_jobs(client, app)
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": new})
    assert r.status_code == 409 and book_row(app, b["id"]).character_generation_id == old
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": new, "confirm_change": True})
    assert r.status_code == 200, r.text
    assert r.json()["character_generation_id"] == new and r.json()["character_description"] == "小狗"
    # 既有的角色動作留著；新的角色動作用新的角色
    assert get_page(app, p["id"]).pose_generation_id == pose
    newer = gen_pose(client, app, p["id"], run=False)
    assert json.loads(gen_row(app, newer).request_json)["character_image"] == gen_row(app, new).image_path
    # 確認換成同一個角色：沒事
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": new, "confirm_change": True})
    assert r.status_code == 200
