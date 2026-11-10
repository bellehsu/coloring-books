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


def test_preflight_reload_updates_engine_registry(settings_file, object_info_subset, monkeypatch):
    from tests.test_preflight import tiny_object_info
    from app.services.comfy_engine import ComfyEngine
    from app.services.workflow_registry import WorkflowRegistry

    fake = FakeComfy(object_info=tiny_object_info(object_info_subset))
    monkeypatch.setattr(app_main, "ComfyClient", lambda url, **kw: fake.client(**kw))
    s = load_settings(settings_file)
    old = WorkflowRegistry(s.paths.workflows_dir).load()
    engine = ComfyEngine(client_factory=lambda: fake.client(), registry=old, run_timeout_s=5)
    app = app_main.create_app(s, engine=engine, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/api/system/preflight?reload=true").status_code == 200
        assert app.state.registry is not old
        assert engine.registry is app.state.registry


def test_system_mode_endpoints_removed(settings_file):
    """Phase 9：只剩 Qwen 一種背景風格與一種角色動作模式，可用性端點已移除。"""
    from tests.fake_engine import FakeEngine

    app = app_main.create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False,
                              startup_preflight=False)
    with TestClient(app) as client:
        assert client.get("/api/system/pose-modes").status_code == 404
        assert client.get("/api/system/background-styles").status_code == 404
