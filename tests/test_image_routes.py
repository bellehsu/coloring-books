"""圖片端點：存放的物件回 302 到簽章網址；/api/files 驗簽章後串流（LocalStorage）。"""

import time

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.models import Generation
from tests.fake_engine import FakeEngine
from tests.test_api_lineart import color_png, selected_page
from tests.test_api_pages import book_with_character  # noqa: F401  (fixtures 由 selected_page 使用)


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _redirects(client, url):
    return client.get(url, follow_redirects=False)


def test_image_route_redirects_to_signed_url(client, app):
    _, p, ids = selected_page(client, app)
    gid = ids[0]
    r = _redirects(client, f"/api/generations/{gid}/image")
    assert r.status_code == 302
    assert r.headers["cache-control"] == "private, max-age=60"
    loc = r.headers["location"]
    assert loc.startswith("/api/files/") and "exp=" in loc and "sig=" in loc
    with app.state.sessions() as s:
        key = s.get(Generation, gid).image_path
    assert key in loc
    followed = client.get(loc)
    assert followed.status_code == 200 and followed.headers["content-type"] == "image/png"
    assert followed.content == app.state.storage.get(key)
    assert client.get(f"/api/generations/{gid}/image").content == followed.content  # 預設跟隨轉址


def test_lineart_and_preview_redirect(client, app):
    _, p, ids = selected_page(client, app)
    for url in (f"/api/pages/{p['id']}/lineart", f"/api/generations/{ids[0]}/lineart-preview"):
        r = _redirects(client, url)
        assert r.status_code == 302, url
        assert r.headers["cache-control"] == "private, max-age=60"
        body = client.get(r.headers["location"])
        assert body.status_code == 200 and body.content.startswith(b"\x89PNG")


def test_processed_routes_still_stream(client, app):
    _, p, ids = selected_page(client, app)
    r = _redirects(client, f"/api/pages/{p['id']}/qa/overlay.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")


def test_image_route_missing_object_404(client, app):
    _, p, ids = selected_page(client, app)
    with app.state.sessions() as s:
        key = s.get(Generation, ids[0]).image_path
    app.state.storage.delete(key)
    r = _redirects(client, f"/api/generations/{ids[0]}/image")
    assert r.status_code == 404 and r.json()["detail"] == "圖片檔案遺失"
    r = _redirects(client, f"/api/pages/{p['id']}/lineart")
    assert r.status_code == 404 and r.json()["detail"] == "圖片檔案遺失"
    r = _redirects(client, f"/api/generations/{ids[0]}/lineart-preview")
    assert r.status_code == 404


def test_mask_missing_object_404_and_redirect(client, app):
    _, p, ids = selected_page(client, app)
    st = app.state.storage
    with app.state.sessions() as s:
        g = s.get(Generation, ids[0])
        g.mask_path = "books/1/generations/gone_mask.png"
        s.commit()
    assert _redirects(client, f"/api/generations/{ids[0]}/mask").status_code == 404
    st.put(g.mask_path, color_png())
    r = _redirects(client, f"/api/generations/{ids[0]}/mask")
    assert r.status_code == 302 and client.get(r.headers["location"]).content == color_png()


def test_files_route_rejects_bad_sig_and_expired(client, app):
    st = app.state.storage
    key = "books/1/generations/x.png"
    st.put(key, b"\x89PNGdata")
    good = st.signed_url(key, 60)
    assert client.get(good).status_code == 200
    assert client.get(good[:-4] + "0000").status_code == 403
    assert client.get(f"/api/files/{key}").status_code == 403  # 沒簽章
    expired = st.signed_url(key, 60, now=time.time() - 3600)
    assert client.get(expired).status_code == 403
    other = st.signed_url("books/1/generations/y.png", 60)  # 簽的是別的 key
    sig_part = other.split("?", 1)[1]
    assert client.get(f"/api/files/{key}?{sig_part}").status_code == 403


def test_files_route_missing_object_with_valid_sig_404(client, app):
    url = app.state.storage.signed_url("books/1/generations/none.png", 60)
    assert client.get(url).status_code == 404


def test_files_route_rejects_escape(client, app):
    st = app.state.storage
    exp = int(time.time()) + 60
    for path in ("books/1/../../x.png", "%2e%2e/secret", "..%2f..%2fetc/passwd", "/etc/passwd", "books//x.png"):
        r = client.get(f"/api/files/{path}?exp={exp}&sig={'0' * 64}")
        assert r.status_code in (403, 404), path
    # 即使簽章對（簽了不合法的 key 是不可能的），簽章函式本身也拒絕
    assert st.verify("../x.png", exp, "0" * 64) is False
