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
from tests.test_api_books import run_jobs
from tests.test_api_lineart import A5_HOME, color_png, get_page, make_passed, set_page
from tests.test_api_pages import book_with_character, new_page


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def book_pages(client, app, n=3, **kw):
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **(kw or A5_HOME))
    pages = []
    for _ in range(n):
        p = new_page(client, b["id"])
        g = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 1}).json()
        run_jobs(client, app, 1)
        r = client.post(f"/api/pages/{p['id']}/select", json={"generation_id": g[0]["id"]})
        assert r.status_code == 200, r.text
        pages.append(r.json())
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
        app.state.storage.abs(s2.image_path).unlink()
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
