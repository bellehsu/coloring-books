from fastapi.testclient import TestClient

from app import main as app_main
from app.config import load_settings
from tests.fake_comfy import FakeComfy


def test_preflight_endpoint(settings_file, object_info_subset, monkeypatch):
    from tests.test_preflight import tiny_object_info

    fake = FakeComfy(object_info=tiny_object_info(object_info_subset))
    monkeypatch.setattr(app_main, "ComfyClient", lambda url, **kw: fake.client(**kw))
    app = app_main.create_app(load_settings(settings_file))
    with TestClient(app) as client:
        r = client.get("/api/system/preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["comfy_reachable"]
    assert body["workflows"][0]["name"] == "tiny"


def test_app_starts_when_comfy_down(settings_file, monkeypatch):
    import httpx

    from app.services.comfy_client import ComfyClient

    def boom(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(app_main, "ComfyClient",
                        lambda url, **kw: ComfyClient(url, transport=httpx.MockTransport(boom), **kw))
    app = app_main.create_app(load_settings(settings_file))
    with TestClient(app) as client:
        body = client.get("/api/system/preflight").json()
    assert body["ok"] is False and body["comfy_reachable"] is False
    assert "無法連線" in body["comfy_error"]
