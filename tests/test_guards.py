"""請求防護：body 大小上限、跨站寫入、簽章網址 TTL 下限（B1、B2、B11）。"""

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api import compose
from app.config import SettingsError, load_settings
from app.main import create_app
from tests.fake_engine import FakeEngine

UPLOAD = "/api/pages/1/backgrounds/upload"


@pytest.fixture
def client(settings_file):
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_handler(monkeypatch):
    """handler 一碰到 body 就是 bug：read_upload 被呼叫就讓測試失敗。"""
    def boom(*a, **k):
        raise AssertionError("handler reached")
    monkeypatch.setattr(compose, "read_upload", boom)


def test_upload_over_limit_413_before_auth(client, no_handler):
    client.cookies.clear()
    r = client.post(UPLOAD, files={"file": ("a.png", b"x" * 12_000_000, "image/png")})
    assert r.status_code == 413
    assert r.json() == {"detail": "檔案太大"}


def test_chunked_upload_over_limit_413(client, no_handler):
    def gen():  # 沒有 Content-Length（chunked）：FastAPI 解析 multipart 時才會讀 body
        yield b'--B\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n\r\n'
        for _ in range(12):
            yield b"x" * 1_000_000
        yield b"\r\n--B--\r\n"
    r = client.post(UPLOAD, content=gen(), headers={"content-type": "multipart/form-data; boundary=B"})
    assert r.status_code == 413
    assert r.json() == {"detail": "檔案太大"}


def test_json_over_2mb_413(client):
    r = client.put("/api/pages/1/lineart/edits", content=b"[" + b"1," * 1_500_000 + b"1]",
                   headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_normal_upload_passes(client):
    r = client.post(UPLOAD, files={"file": ("a.png", b"x" * 1_000_000, "image/png")})
    assert r.status_code != 413  # 頁面 1 不存在或圖片無效都可以，只要不是被大小擋下


def test_cross_site_post_403(client):
    r = client.post("/api/books", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.json() == {"detail": "不接受從其他網站送來的修改"}


@pytest.mark.parametrize("site", [None, "same-origin", "same-site", "none"])
def test_same_origin_and_missing_header_allowed(client, site):
    headers = {"Sec-Fetch-Site": site} if site else {}
    r = client.post("/api/books", json={}, headers=headers)
    assert r.status_code != 403


def test_get_cross_site_allowed(client):
    r = client.get("/api/books", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


def test_ttl_below_90_rejected(settings_file):
    data = yaml.safe_load(settings_file.read_text())
    data["storage"] = {"signed_url_ttl_s": 89}
    settings_file.write_text(yaml.safe_dump(data))
    with pytest.raises(SettingsError):
        load_settings(settings_file)
    data["storage"] = {"signed_url_ttl_s": 90}
    settings_file.write_text(yaml.safe_dump(data))
    assert load_settings(settings_file).storage.signed_url_ttl_s == 90
