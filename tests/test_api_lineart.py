import io
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import Book, CharacterCheck, LineartEdit, Page, PageStatus, utcnow
from app.services.layout import PrintMode
from app.services.qa import QA_MESSAGES, QAReport
from tests.fake_engine import FakeEngine, make_png
from tests.test_api_books import new_book, run_jobs
from tests.test_api_pages import book_with_character, new_page, upload


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# A5 家用：content_px 1512×2244、generation_size 832×1216（測試跑得快）
A5_HOME = {"paper_size": "a5", "print_mode": "home"}


def color_png(size=(832, 1216), gap=0) -> bytes:
    """粉紅色方塊、黑色外框；gap > 0 時在上緣中間留一個缺口（會漏色）。"""
    w, h = size
    im = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([150, 200, w - 150, h - 200], fill=(250, 200, 200), outline=(0, 0, 0), width=6)
    if gap:
        cx = w // 2
        d.rectangle([cx - gap // 2, 195, cx + gap // 2, 210], fill=(250, 200, 200))
        d.rectangle([cx - gap // 2, 195, cx + gap // 2, 199], fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def selected_page(client, app, *, gap=0, count=1, book_kw=None):
    """建書＋角色＋一頁，產生 count 張候選並選第一張。回傳 (book, page, [gen ids])。"""
    app.state.engine.image = color_png(gap=gap)
    b, _ = book_with_character(client, app, **(book_kw or A5_HOME))
    p = new_page(client, b["id"])
    gens = client.post(f"/api/pages/{p['id']}/candidates", json={"count": count}).json()
    run_jobs(client, app, count)
    ids = [g["id"] for g in gens]
    r = client.post(f"/api/pages/{p['id']}/select", json={"generation_id": ids[0]})
    assert r.status_code == 200, r.text
    return b, r.json(), ids


def set_page(app, page_id, **values):
    with app.state.sessions() as s:
        page = s.get(Page, page_id)
        for k, v in values.items():
            setattr(page, k, v)
        s.add(page)
        s.commit()


def get_page(app, page_id) -> Page:
    with app.state.sessions() as s:
        return s.get(Page, page_id)


def make_passed(app, page_id, **extra):
    set_page(app, page_id, status=PageStatus.PASSED, passed_at=utcnow(),
             character_check=CharacterCheck.CONSISTENT, **extra)


def png(resp) -> Image.Image:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(resp.content))


def page_out(client, book_id, page_id):
    return next(p for p in client.get(f"/api/books/{book_id}/pages").json() if p["id"] == page_id)


def edits_body(gen_id, strokes):
    return {"generation_id": gen_id, "strokes": strokes}


DRAW = {"op": "draw", "width_px": 12, "points": [[0.2, 0.5], [0.8, 0.5]]}


def test_lineart_png_and_qa(client, app):
    b, p, _ = selected_page(client, app)
    im = png(client.get(f"/api/pages/{p['id']}/lineart"))
    assert im.mode == "1"
    assert list(im.size) == b["layout"]["content_px"]
    qa = client.get(f"/api/pages/{p['id']}/qa").json()
    assert qa["gap_count"] == 0 and qa["errors"] == [] and qa["gaps"] == []
    assert qa["status"] == "draft" and qa["character_check"] is None
    assert qa["qa_note"] == "" and qa["forced_pass_reason"] is None
    assert "dangling_count" in qa and "region_count" in qa["metrics"]
    assert qa["lineart_url"].startswith(f"/api/pages/{p['id']}/lineart?v=")
    assert page_out(client, b["id"], p["id"])["lineart_url"] == qa["lineart_url"]
    assert png(client.get(qa["lineart_url"])).size == im.size


def test_qa_reports_gap_errors_with_messages(client, app):
    b, p, _ = selected_page(client, app, gap=12)
    qa = client.get(f"/api/pages/{p['id']}/qa").json()
    assert qa["gap_count"] == 1 and len(qa["gaps"]) == 1
    assert qa["errors"] == [{"code": "gaps", "message": QA_MESSAGES["gaps"]}]
    assert all(set(w) == {"code", "message"} for w in qa["warnings"])
    assert page_out(client, b["id"], p["id"])["needs_fix"] is True


def test_lineart_requires_selection(client, app):
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"])
    for url in (f"/api/pages/{p['id']}/lineart", f"/api/pages/{p['id']}/qa",
                f"/api/pages/{p['id']}/qa/overlay.png", f"/api/pages/{p['id']}/lineart/edits"):
        r = client.get(url)
        assert r.status_code == 409 and r.json()["detail"] == "這一頁還沒有選定圖片", url
        assert client.get(url.replace(f"/pages/{p['id']}/", "/pages/999/")).status_code == 404
    r = client.put(f"/api/pages/{p['id']}/lineart/edits", json=edits_body(1, []))
    assert r.status_code == 409
    r = client.post(f"/api/pages/{p['id']}/lineart/autofix", json={"generation_id": 1})
    assert r.status_code == 409
    assert p["lineart_url"] is None and p["needs_fix"] is None and p["aspect_mismatch"] is False


def test_lineart_missing_image_is_409(client, app):
    _, p, ids = selected_page(client, app)
    from app.models import Generation

    with app.state.sessions() as s:
        app.state.storage.abs(s.get(Generation, ids[0]).image_path).unlink()
    r = client.get(f"/api/pages/{p['id']}/lineart")
    assert r.status_code == 409 and r.json()["detail"] == "選定的圖片檔案遺失，請重新選圖或重新生成"


def test_params_change_rerenders_and_demotes(client, app):
    b, p, _ = selected_page(client, app)
    qa = client.get(f"/api/pages/{p['id']}/qa").json()
    first = client.get(f"/api/pages/{p['id']}/lineart").content
    make_passed(app, p["id"])
    assert page_out(client, b["id"], p["id"])["needs_fix"] is False

    r = client.patch(f"/api/pages/{p['id']}", json={"line_width_px": 14})
    assert r.status_code == 200
    out = r.json()
    assert out["line_width_px"] == 14 and out["status"] == "draft"
    assert out["character_check"] == "consistent"  # 彩圖沒變，角色檢查保留
    assert out["lineart_url"] != qa["lineart_url"]
    assert out["needs_fix"] is None  # 舊 QA 已不對應目前的參數
    assert get_page(app, p["id"]).passed_at is None
    assert client.get(out["lineart_url"]).content != first
    assert client.get(f"/api/pages/{p['id']}/qa").json()["lineart_url"] == out["lineart_url"]
    assert page_out(client, b["id"], p["id"])["needs_fix"] is False

    # 值沒變 → 不退回
    make_passed(app, p["id"])
    r = client.patch(f"/api/pages/{p['id']}", json={"line_width_px": 14, "line_detail": "standard"})
    assert r.json()["status"] == "passed"
    # 細緻度改變 → 退回
    r = client.patch(f"/api/pages/{p['id']}", json={"line_detail": "detailed"})
    assert r.json()["status"] == "draft" and r.json()["line_detail"] == "detailed"


def test_line_width_range(client):
    p = new_page(client, new_book(client)["id"])
    assert p["line_width_px"] == 11 and p["line_detail"] == "standard"
    for bad in (7, 17):
        assert client.patch(f"/api/pages/{p['id']}", json={"line_width_px": bad}).status_code == 422
    for ok in (8, 16):
        r = client.patch(f"/api/pages/{p['id']}", json={"line_width_px": ok})
        assert r.status_code == 200 and r.json()["line_width_px"] == ok
    assert client.patch(f"/api/pages/{p['id']}", json={"line_detail": "fine"}).status_code == 422


def test_edits_roundtrip_and_apply(client, app):
    _, p, ids = selected_page(client, app)
    url = f"/api/pages/{p['id']}/lineart/edits"
    assert client.get(url).json() == {"generation_id": ids[0], "strokes": []}
    before = client.get(f"/api/pages/{p['id']}/lineart").content
    make_passed(app, p["id"])
    strokes = [DRAW, {"op": "erase", "width_px": 20.5, "points": [[0.5, 0.0]]}]
    r = client.put(url, json=edits_body(ids[0], strokes))
    assert r.status_code == 200, r.text
    qa = r.json()
    assert qa["status"] == "draft" and "gap_count" in qa
    assert client.get(url).json() == {"generation_id": ids[0], "strokes": strokes}
    after = client.get(qa["lineart_url"]).content
    assert after != before
    assert get_page(app, p["id"]).character_check == CharacterCheck.CONSISTENT
    # 同樣的筆畫再送一次不算修改：不退回
    make_passed(app, p["id"])
    assert client.put(url, json=edits_body(ids[0], strokes)).json()["status"] == "passed"
    # 清空
    r = client.put(url, json=edits_body(ids[0], []))
    assert r.status_code == 200 and client.get(url).json()["strokes"] == []
    assert client.get(f"/api/pages/{p['id']}/lineart").content == before


def edit_rows(app):
    with app.state.sessions() as s:
        return s.exec(select(LineartEdit)).all()


def test_edits_reject_stale_generation(client, app):
    _, p, ids = selected_page(client, app, count=2)
    url = f"/api/pages/{p['id']}/lineart/edits"
    r = client.put(url, json=edits_body(ids[1], [DRAW]))
    assert r.status_code == 409 and r.json()["detail"]
    assert edit_rows(app) == []


def test_edits_rejected_when_selection_changes_mid_request(client, app):
    from sqlalchemy import event

    _, p, ids = selected_page(client, app, count=2)
    fired = []

    def switch_before_update(conn, cursor, statement, params, context, executemany):
        # 檢查通過後、寫入修改圖層前（交易還沒拿到寫入鎖），另一請求換了選圖
        if statement.startswith("INSERT INTO lineartedit") and not fired:
            fired.append(1)
            with app.state.sessions() as other:
                row = other.get(Page, p["id"])
                row.selected_generation_id = ids[1]
                other.add(row)
                other.commit()

    event.listen(app.state.db, "before_cursor_execute", switch_before_update)
    try:
        r = client.put(f"/api/pages/{p['id']}/lineart/edits", json=edits_body(ids[0], [DRAW]))
    finally:
        event.remove(app.state.db, "before_cursor_execute", switch_before_update)
    assert fired and r.status_code == 409
    assert edit_rows(app) == []


def test_edits_validation(client, app):
    _, p, ids = selected_page(client, app)
    url = f"/api/pages/{p['id']}/lineart/edits"

    def stroke(**kw):
        return {**DRAW, **kw}

    bad = [
        [stroke(points=[[1.5, 0.5]])],
        [stroke(points=[[-0.1, 0.5]])],
        [stroke(points=[["NaN", 0.5]])],
        [stroke(points=[["inf", 0.5]])],
        [stroke(points=[[0.5, 0.5, 0.5]])],
        [stroke(points=[])],
        [stroke(points=[[0.5, 0.5]] * 2001)],
        [stroke(points=[[0.5, 0.5]] * 2000)] * 10 + [stroke(points=[[0.5, 0.5]])],  # 20001 點
        [stroke(op="fill")],
        [stroke(width_px=1)],
        [stroke(width_px=65)],
        [stroke(width_px="NaN")],
        [stroke(points=[[0.5, 0.5]])] * 501,
    ]
    for strokes in bad:
        r = client.put(url, json=edits_body(ids[0], strokes))
        assert r.status_code == 422, (strokes[0], len(strokes))
    ok = [stroke(points=[[0.0, 1.0]] * 2000)] * 10  # 剛好 20000 點
    assert client.put(url, json=edits_body(ids[0], ok)).status_code == 200
    assert client.put(url, json=edits_body(ids[0], [stroke(width_px=2), stroke(width_px=64)])).status_code == 200


def test_edits_follow_generation(client, app):
    _, p, ids = selected_page(client, app, count=2)
    url = f"/api/pages/{p['id']}/lineart/edits"
    assert client.put(url, json=edits_body(ids[0], [DRAW])).status_code == 200
    client.post(f"/api/pages/{p['id']}/select", json={"generation_id": ids[1]})
    assert client.get(url).json() == {"generation_id": ids[1], "strokes": []}
    client.post(f"/api/pages/{p['id']}/select", json={"generation_id": ids[0]})
    assert client.get(url).json() == {"generation_id": ids[0], "strokes": [DRAW]}


def test_autofix_appends_bridges(client, app):
    b, p, ids = selected_page(client, app, gap=12, count=2)
    url = f"/api/pages/{p['id']}/lineart/autofix"
    edits = f"/api/pages/{p['id']}/lineart/edits"
    before = client.get(f"/api/pages/{p['id']}/qa").json()
    assert before["gap_count"] == 1
    # 使用者已有一筆不影響缺口的修線
    user = {"op": "erase", "width_px": 4, "points": [[0.01, 0.01]]}
    assert client.put(edits, json=edits_body(ids[0], [user])).status_code == 200

    r = client.post(url, json={"generation_id": ids[1]})
    assert r.status_code == 409
    assert client.get(edits).json()["strokes"] == [user]

    make_passed(app, p["id"], forced_pass_reason="趕時間")
    r = client.post(url, json={"generation_id": ids[0]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["autofix_added"] == 1
    assert out["gap_count"] == 0 and out["errors"] == []
    assert out["status"] == "draft" and out["character_check"] == "consistent"
    strokes = client.get(edits).json()["strokes"]
    assert strokes[0] == user and len(strokes) == 2
    assert strokes[1]["op"] == "draw" and strokes[1]["width_px"] == 11
    assert len(strokes[1]["points"]) == 2
    assert all(0 <= v <= 1 for pt in strokes[1]["points"] for v in pt)
    assert out["lineart_url"] == page_out(client, b["id"], p["id"])["lineart_url"]

    # 已經沒有缺口：200，不新增筆畫，也不退回
    make_passed(app, p["id"])
    r = client.post(url, json={"generation_id": ids[0]})
    assert r.status_code == 200 and r.json()["autofix_added"] == 0
    assert r.json()["status"] == "passed"
    assert len(client.get(edits).json()["strokes"]) == 2


def _fake_report(n):
    bridges = [((0.1 + 0.05 * i, 0.3), (0.1 + 0.05 * i, 0.31)) for i in range(n)]
    return QAReport(errors=["gaps"] if n else [], warnings=[], gaps=[(0.1, 0.3)] * n,
                    dangling_count=2 * n, gap_count=n, bridges=bridges)


@pytest.mark.parametrize("counts, added", [
    ([4, 3, 2, 1], 4 + 3 + 2),  # 最多 3 輪
    ([3, 2, 2], 3),  # 第二輪沒減少：那一輪的筆畫不留，停止
    ([3, 5], 0),  # 第一輪反而變多：什麼都不加
    ([2, 0], 2),
])
def test_autofix_passes(client, app, monkeypatch, counts, added):
    from app.api import lineart as lineart_api
    from app.services import page_lineart

    _, p, ids = selected_page(client, app)
    seq = iter(counts)
    initial = _fake_report(next(seq))
    # 頁面目前的 QA（ensure_page_lineart 用的 evaluate）：先是初始缺口，存檔後重算
    final = {"n": None}
    monkeypatch.setattr(page_lineart, "evaluate",
                        lambda ink, lw: initial if final["n"] is None else _fake_report(final["n"]))
    pass_reports = [_fake_report(n) for n in seq]

    def fake_eval(ink, lw):
        rep = pass_reports.pop(0)
        return rep

    monkeypatch.setattr(lineart_api, "evaluate", fake_eval)
    client.get(f"/api/pages/{p['id']}/qa")
    final["n"] = 0
    r = client.post(f"/api/pages/{p['id']}/lineart/autofix", json={"generation_id": ids[0]})
    assert r.status_code == 200, r.text
    assert r.json()["autofix_added"] == added
    strokes = client.get(f"/api/pages/{p['id']}/lineart/edits").json()["strokes"]
    assert len(strokes) == added
    assert all(s["op"] == "draw" and s["width_px"] == 11 for s in strokes)


def test_autofix_respects_edit_layer_limits(client, app, monkeypatch):
    from app.services import page_lineart

    _, p, ids = selected_page(client, app)
    monkeypatch.setattr(page_lineart, "evaluate", lambda ink, lw: _fake_report(3))
    full = [{"op": "erase", "width_px": 4, "points": [[0.01, 0.01]]}] * 500
    assert client.put(f"/api/pages/{p['id']}/lineart/edits", json=edits_body(ids[0], full)).status_code == 200
    r = client.post(f"/api/pages/{p['id']}/lineart/autofix", json={"generation_id": ids[0]})
    assert r.status_code == 409 and "上限" in r.json()["detail"]
    assert len(client.get(f"/api/pages/{p['id']}/lineart/edits").json()["strokes"]) == 500


def test_select_resets_character_check(client, app):
    _, p, ids = selected_page(client, app, count=2)
    make_passed(app, p["id"], forced_pass_reason="原因")
    # 選同一張：不算換圖
    r = client.post(f"/api/pages/{p['id']}/select", json={"generation_id": ids[0]})
    assert r.json()["status"] == "passed" and r.json()["character_check"] == "consistent"
    r = client.post(f"/api/pages/{p['id']}/select", json={"generation_id": ids[1]})
    out = r.json()
    assert out["status"] == "draft" and out["character_check"] is None
    page = get_page(app, p["id"])
    assert page.forced_pass_reason is None and page.passed_at is None
    assert page.status == PageStatus.DRAFT


def test_overlay_png(client, app):
    b, p, _ = selected_page(client, app, gap=12, book_kw={"paper_size": "a5", "print_mode": "print_shop"})
    qa = client.get(f"/api/pages/{p['id']}/qa").json()
    im = png(client.get(f"/api/pages/{p['id']}/qa/overlay.png"))
    assert im.mode == "RGB" and list(im.size) == b["layout"]["content_px"]
    w, h = im.size
    (gx, gy), = qa["gaps"]
    cx, cy = round(gx * w), round(gy * h)
    red = [im.getpixel((cx, cy - 40 + d)) for d in range(-3, 4)]
    assert (255, 0, 0) in red
    x0, y0, x1, y1 = b["layout"]["trim_box_px"]
    assert im.getpixel((x0 + 1, (y0 + y1) // 2)) == (0, 0, 255)
    sx0, sy0, sx1, sy1 = b["layout"]["safe_box_px"]
    r_, g_, b_ = im.getpixel((sx0 + 1, (sy0 + sy1) // 2))
    assert g_ > 100 and r_ == 0 and b_ == 0
    # 家用模式不畫框
    _, p2, _ = selected_page(client, app)
    im2 = png(client.get(f"/api/pages/{p2['id']}/qa/overlay.png"))
    colors = {c for _, c in im2.getcolors(maxcolors=1 << 16)}
    assert colors <= {(0, 0, 0), (255, 255, 255)}


def test_generation_preview(client, app):
    app.state.engine.image = color_png()
    b, char_gen = book_with_character(client, app, **A5_HOME)
    url = f"/api/generations/{char_gen}/lineart-preview"
    im = png(client.get(url))
    assert im.mode == "1" and list(im.size) == b["layout"]["content_px"]
    thin = client.get(url, params={"detail": "detailed", "line_width_px": 8})
    assert png(thin).size == im.size and thin.content != client.get(url).content
    assert client.get(url, params={"line_width_px": 17}).status_code == 422
    assert client.get(url, params={"detail": "fine"}).status_code == 422
    p = new_page(client, b["id"])
    queued = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 1}).json()[0]
    assert client.get(f"/api/generations/{queued['id']}/lineart-preview").status_code == 404
    run_jobs(client, app)
    assert png(client.get(f"/api/generations/{queued['id']}/lineart-preview")).size == im.size
    assert client.get("/api/generations/999/lineart-preview").status_code == 404


def test_reference_sketch(client):
    b = new_book(client)
    im = Image.new("RGB", (300, 200), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([40, 40, 260, 160], outline=(0, 0, 0), width=4)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    url = f"/api/books/{b['id']}/reference-sketch"
    out = png(client.post(url, files={"file": ("ref.png", buf.getvalue(), "image/png")}))
    assert list(out.size) == b["layout"]["generation_size"]
    gray = out.convert("L")
    assert gray.getpixel((0, 0)) == 255 and gray.getpixel((out.width - 1, out.height - 1)) == 255
    assert gray.getextrema()[0] < 64  # 有黑線
    r = client.post(url, files={"file": ("a.txt", b"not an image", "text/plain")})
    assert r.status_code == 400


def test_reference_sketch_downscales_large_upload_before_extracting(client, monkeypatch):
    from app.api import lineart as lineart_api

    b = new_book(client)
    gw, gh = b["layout"]["generation_size"]
    seen = []
    real = lineart_api.extract
    monkeypatch.setattr(lineart_api, "extract", lambda img, spec: seen.append(img.shape) or real(img, spec))
    im = Image.new("RGB", (4000, 3000), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([400, 400, 3600, 2600], outline=(0, 0, 0), width=12)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = png(client.post(f"/api/books/{b['id']}/reference-sketch",
                          files={"file": ("big.png", buf.getvalue(), "image/png")}))
    assert list(out.size) == [gw, gh]
    (h, w, _), = seen
    assert w <= gw and h <= gh and (w == gw or h == gh)
    assert out.convert("L").getextrema()[0] < 64
    assert client.post("/api/books/999/reference-sketch",
                       files={"file": ("ref.png", buf.getvalue(), "image/png")}).status_code == 404


def test_page_out_aspect_and_sketch_version(client, app):
    b, p, _ = selected_page(client, app)
    assert p["aspect_mismatch"] is False and p["lineart_url"]
    assert p["line_detail"] == "standard" and p["line_width_px"] == 11
    assert p["needs_fix"] is None  # 還沒算過 QA
    with app.state.sessions() as s:  # 書改成印刷廠：生成尺寸 864×1216 ≠ 選定圖 832×1216
        book = s.get(Book, b["id"])
        book.print_mode = PrintMode.PRINT_SHOP
        s.add(book)
        s.commit()
    assert page_out(client, b["id"], p["id"])["aspect_mismatch"] is True
    r = client.patch(f"/api/pages/{p['id']}", json={"scene_prompt": "公園"})
    assert r.json()["aspect_mismatch"] is True

    r1 = upload(client, p["id"], make_png((128, 128))).json()
    page = get_page(app, p["id"])
    v = round(page.updated_at.astimezone(timezone.utc).timestamp() * 1_000_000)
    assert r1["sketch_url"] == f"/api/pages/{p['id']}/sketch?v={v}"
    r2 = upload(client, p["id"], make_png((256, 256))).json()
    assert r2["sketch_url"] != r1["sketch_url"]
    assert client.get(r2["sketch_url"]).status_code == 200
