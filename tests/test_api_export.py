import io
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import load_settings
from app.main import create_app
from app.models import Generation, PageStatus
from app.services.layout import PaperSize, PrintMode, compute_layout
from tests.fake_engine import FakeEngine
from tests.test_api_lineart import (
    A5_HOME, color_png, get_page, make_passed, page_out, seed_generation, set_page,
)
from tests.test_api_pages import book_with_character, new_page


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def book_pages(client, app, n=3, **kw):
    """建書＋角色＋n 頁，每頁直接寫入一張已完成的合成圖並選用（彩色方塊，沒有缺口）。"""
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **(kw or A5_HOME))
    pages = []
    for _ in range(n):
        p = new_page(client, b["id"])
        set_page(app, p["id"], selected_generation_id=seed_generation(app, b["id"], p["id"], color_png()))
        pages.append(page_out(client, b["id"], p["id"]))
    return b, pages


def npages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b(?!s)", pdf))


def test_export_passed_only_and_order(client, app):
    b, ps = book_pages(client, app)
    make_passed(app, ps[0]["id"])
    make_passed(app, ps[2]["id"])
    set_page(app, ps[2]["id"], position=0)
    set_page(app, ps[0]["id"], position=1)
    r = client.get(f"/api/books/{b['id']}/export.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert npages(r.content) == 2
    r = client.get(f"/api/books/{b['id']}/export.pdf?include=all")
    assert npages(r.content) == 3
    cd = r.headers["content-disposition"]
    assert f'filename="book-{b["id"]}.pdf"' in cd
    assert f"filename*=UTF-8''{quote(b['title'])}.pdf" in cd


def test_export_errors(client, app):
    b, ps = book_pages(client, app, n=3)
    r = client.get(f"/api/books/{b['id']}/export.pdf")
    assert r.status_code == 409
    assert r.json()["detail"] == "沒有可匯出的頁面（需要先通過 QA）"
    assert client.get("/api/books/999/export.pdf").status_code == 404
    make_passed(app, ps[1]["id"])
    with app.state.sessions() as s:
        s2 = s.get(Generation, ps[1]["selected_generation_id"])
        app.state.storage.delete(s2.image_path)
    r = client.get(f"/api/books/{b['id']}/export.pdf")
    assert r.status_code == 409 and "第 2 頁" in r.json()["detail"]


def test_summary_reasons(client, app):
    b, ps = book_pages(client, app)
    extra = new_page(client, b["id"])
    make_passed(app, ps[0]["id"])
    d = client.get(f"/api/books/{b['id']}/export/summary").json()
    assert d["total"] == 4 and d["passed"] == 1
    assert d["exportable_ids"] == [ps[0]["id"]]
    reasons = {s["page_id"]: (s["position"], s["reason"]) for s in d["skipped"]}
    assert reasons[ps[1]["id"]] == (ps[1]["position"], "not_passed")
    assert reasons[extra["id"]][1] == "no_selection"
    assert len(d["skipped"]) == 3


def test_print_png(client, app):
    b, ps = book_pages(client, app, n=1, paper_size="a5", print_mode="print_shop")
    r = client.get(f"/api/pages/{ps[0]['id']}/print.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    im = Image.open(io.BytesIO(r.content))
    assert im.size == compute_layout(PaperSize.A5, PrintMode.PRINT_SHOP).page_px
    assert im.mode == "1" and round(im.info["dpi"][0]) == 300
    new = new_page(client, b["id"])
    assert client.get(f"/api/pages/{new['id']}/print.png").status_code == 409


def test_export_skips_page_demoted_by_rerender(client, app, monkeypatch):
    from app.services import page_lineart
    from app.services.qa import QAReport
    b, ps = book_pages(client, app, n=2)
    for p in ps:
        make_passed(app, p["id"])
    orig = page_lineart.evaluate

    def bad_for_first(ink, w):
        r = orig(ink, w)
        if not hasattr(bad_for_first, "done"):
            bad_for_first.done = True
            return QAReport(errors=["gaps"], warnings=[], gaps=[], dangling_count=0, gap_count=1)
        return r

    monkeypatch.setattr(page_lineart, "evaluate", bad_for_first)
    r = client.get(f"/api/books/{b['id']}/export.pdf")
    assert r.status_code == 200 and npages(r.content) == 1
    assert get_page(app, ps[0]["id"]).status == PageStatus.DRAFT
    # 全部被退回時回 409
    monkeypatch.setattr(page_lineart, "evaluate",
                        lambda ink, w: QAReport(errors=["gaps"], warnings=[], gaps=[],
                                                dangling_count=0, gap_count=1))
    make_passed(app, ps[1]["id"])
    set_page(app, ps[1]["id"], lineart_key=None)
    r = client.get(f"/api/books/{b['id']}/export.pdf")
    assert r.status_code == 409 and r.json()["detail"] == "沒有可匯出的頁面（需要先通過 QA）"


def test_summary_counts_after_rerender(client, app, monkeypatch):
    from app.services import page_lineart
    from app.services.qa import QAReport
    b, ps = book_pages(client, app, n=1)
    make_passed(app, ps[0]["id"])
    monkeypatch.setattr(page_lineart, "evaluate",
                        lambda ink, w: QAReport(errors=["gaps"], warnings=[], gaps=[],
                                                dangling_count=0, gap_count=1))
    set_page(app, ps[0]["id"], lineart_key=None)
    d = client.get(f"/api/books/{b['id']}/export/summary").json()
    assert d["passed"] == 0 and d["skipped"][0]["reason"] == "not_passed"
