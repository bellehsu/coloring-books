import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.models import CharacterCheck, PageStatus
from app.api import review
from tests.fake_engine import FakeEngine
from tests.test_api_lineart import get_page, make_passed, selected_page, set_page


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def check(client, p, result="consistent", note="", gen=None):
    """p 是頁面 dict（帶選定圖片 id）；測 404 時可直接傳 page id。"""
    pid = p["id"] if isinstance(p, dict) else p
    gid = gen if gen is not None else (p["selected_generation_id"] if isinstance(p, dict) else 1)
    return client.post(f"/api/pages/{pid}/character-check",
                       json={"result": result, "note": note, "generation_id": gid})


def test_character_check_saves_result_and_note(client, app):
    _, p, _ = selected_page(client, app)
    r = check(client, p, "consistent", "  耳朵一致  ")
    assert r.status_code == 200
    assert r.json()["character_check"] == "consistent"
    assert get_page(app, p["id"]).qa_note == "耳朵一致"


def test_character_check_note_too_long_422(client, app):
    _, p, _ = selected_page(client, app)
    assert check(client, p, "consistent", "x" * 501).status_code == 422
    assert check(client, p, "consistent", "x" * 500).status_code == 200


def test_character_check_404(client):
    assert check(client, 9999).status_code == 404


def test_inconsistent_demotes_passed_page(client, app):
    _, p, _ = selected_page(client, app)
    make_passed(app, p["id"])
    r = check(client, p, "inconsistent", "顏色不對")
    assert r.status_code == 200
    page = get_page(app, p["id"])
    assert page.status == PageStatus.DRAFT and page.passed_at is None
    assert page.character_check == CharacterCheck.INCONSISTENT


def test_pass_without_selection_409(client, app):
    _, p, _ = selected_page(client, app)
    set_page(app, p["id"], selected_generation_id=None)
    r = client.post(f"/api/pages/{p['id']}/pass", json={})
    assert r.status_code == 409 and r.json()["detail"] == "這一頁還沒有選定圖片"


def test_pass_requires_consistent_character(client, app):
    _, p, _ = selected_page(client, app)
    r = client.post(f"/api/pages/{p['id']}/pass", json={})
    assert r.status_code == 409 and r.json()["detail"] == "請先確認角色外型與設定一致"
    check(client, p, "inconsistent")
    r = client.post(f"/api/pages/{p['id']}/pass", json={"force_reason": "硬過"})
    assert r.status_code == 409 and r.json()["detail"] == "請先確認角色外型與設定一致"


def test_pass_clean_page(client, app):
    _, p, _ = selected_page(client, app)
    check(client, p)
    r = client.post(f"/api/pages/{p['id']}/pass", json={"force_reason": "多餘"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "passed"
    page = get_page(app, p["id"])
    assert page.passed_at is not None and page.forced_pass_reason is None


def test_pass_with_errors_needs_reason(client, app):
    _, p, _ = selected_page(client, app, gap=12)
    check(client, p)
    for body in ({}, {"force_reason": "   "}, {"force_reason": None}):
        r = client.post(f"/api/pages/{p['id']}/pass", json=body)
        assert r.status_code == 409
        d = r.json()["detail"]
        assert d["message"] == "自動檢查有錯誤，修線後再通過，或填寫原因強制通過"
        assert d["errors"] and set(d["errors"][0]) == {"code", "message"}
    assert get_page(app, p["id"]).status == PageStatus.DRAFT


def test_force_pass_records_reason(client, app):
    _, p, _ = selected_page(client, app, gap=12)
    check(client, p)
    r = client.post(f"/api/pages/{p['id']}/pass", json={"force_reason": "  故意留縫  "})
    assert r.status_code == 200, r.text
    page = get_page(app, p["id"])
    assert page.status == PageStatus.PASSED
    assert page.forced_pass_reason == "故意留縫" and page.passed_at is not None


def test_pass_rejected_if_lineart_changed_concurrently(client, app, monkeypatch):
    _, p, _ = selected_page(client, app)
    check(client, p)
    real = review.ensure_page_lineart

    def racy(session, storage, page_id):
        lin = real(session, storage, page_id)
        set_page(app, page_id, line_width_px=15, lineart_key="changed-elsewhere")
        return lin

    monkeypatch.setattr(review, "ensure_page_lineart", racy)
    r = client.post(f"/api/pages/{p['id']}/pass", json={})
    assert r.status_code == 409 and r.json()["detail"] == "頁面剛被修改，請重新檢查"
    assert get_page(app, p["id"]).status == PageStatus.DRAFT


def test_unpass(client, app):
    _, p, _ = selected_page(client, app)
    make_passed(app, p["id"], forced_pass_reason="x")
    r = client.post(f"/api/pages/{p['id']}/unpass")
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert get_page(app, p["id"]).passed_at is None
    assert client.post("/api/pages/9999/unpass").status_code == 404


def test_character_check_stale_generation_409(client, app):
    _, p, ids = selected_page(client, app, count=2)
    r = check(client, p, "consistent", gen=ids[1])
    assert r.status_code == 409 and r.json()["detail"] == "選定的圖片已更換，請重新檢查"
    assert get_page(app, p["id"]).character_check is None
    assert client.post(f"/api/pages/{p['id']}/character-check",
                       json={"result": "consistent"}).status_code == 422


def test_force_reason_too_long_422(client, app):
    _, p, _ = selected_page(client, app, gap=12)
    check(client, p)
    r = client.post(f"/api/pages/{p['id']}/pass", json={"force_reason": "x" * 501})
    assert r.status_code == 422
    r = client.post(f"/api/pages/{p['id']}/pass", json={"force_reason": "x" * 500})
    assert r.status_code == 200
