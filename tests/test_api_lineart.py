import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import (
    Book, CharacterCheck, Generation, GenKind, GenStatus, LineartEdit, Page, PageStatus, StrokeLayer, utcnow,
)
from app.services.lineart_render import Stroke
from app.services.layout import PrintMode
from app.services.qa import QA_MESSAGES, QAReport
from tests.fake_engine import FakeEngine
from tests.test_api_books import new_book, run_jobs
from tests.test_api_pages import book_with_character, new_page


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


def seed_generation(app, book_id, page_id, png: bytes, kind=GenKind.COMPOSITE) -> int:
    """直接寫入一筆成功、有圖的 generation（預設合成圖，現行流程的頁面選定圖），回傳 id。不選用。"""
    with Image.open(io.BytesIO(png)) as im:
        width, height = im.size
    with app.state.sessions() as s:
        g = Generation(book_id=book_id, page_id=page_id, kind=kind, status=GenStatus.SUCCEEDED,
                       user_text="", prompt="", negative_prompt="", seed=0, width=width, height=height,
                       request_json="{}")
        s.add(g)
        s.flush()
        rel = app.state.storage.generation_image(book_id, g.id)
        app.state.storage.put(rel, png)
        g.image_path = rel
        s.commit()
        return g.id


def selected_page(client, app, *, gap=0, count=1, book_kw=None, page_kw=None, kind=GenKind.COMPOSITE):
    """建書＋角色＋一頁，直接寫入 count 張已完成的選定圖候選（預設合成圖；彩色方塊）並選第一張。
    回傳 (book, page, [gen ids])。"""
    app.state.engine.image = color_png(gap=gap)
    b, _ = book_with_character(client, app, **(book_kw or A5_HOME))
    p = new_page(client, b["id"], **(page_kw or {}))
    ids = [seed_generation(app, b["id"], p["id"], color_png(gap=gap), kind) for _ in range(count)]
    set_page(app, p["id"], selected_generation_id=ids[0])
    return b, page_out(client, b["id"], p["id"]), ids


def legacy_page(client, app, **page_kw):
    """舊流程頁面：選定圖是一張成功的整頁候選圖（kind PAGE）。建立整頁候選的端點已停用，
    所以直接寫進 DB（與舊資料長得一樣）。回傳 (book, page, [gen id])，同 selected_page。"""
    return selected_page(client, app, page_kw=page_kw, kind=GenKind.PAGE)


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


def test_lineart_missing_image(client, app):
    """選定圖片的物件遺失：線稿圖片端點回 404「圖片檔案遺失」；JSON 端點（QA）仍回 409 並說明怎麼辦。"""
    _, p, ids = selected_page(client, app)
    from app.models import Generation

    with app.state.sessions() as s:
        app.state.storage.delete(s.get(Generation, ids[0]).image_path)
    r = client.get(f"/api/pages/{p['id']}/lineart")
    assert r.status_code == 404 and r.json()["detail"] == "圖片檔案遺失"
    r = client.get(f"/api/pages/{p['id']}/qa")
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
    set_page(app, p["id"], selected_generation_id=ids[1])
    assert client.get(url).json() == {"generation_id": ids[1], "strokes": []}
    set_page(app, p["id"], selected_generation_id=ids[0])
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
    # 補線寫進 auto 層；使用者的修線（edits API 讀寫的 user 層）不變
    assert client.get(edits).json()["strokes"] == [user]
    strokes = layer_strokes(app, ids[0], StrokeLayer.AUTO)
    assert len(strokes) == 1
    assert strokes[0].op == "draw" and strokes[0].width_px == 11
    assert len(strokes[0].points) == 2
    assert all(0 <= v <= 1 for pt in strokes[0].points for v in pt)
    assert [(x.op, x.width_px, x.points) for x in layer_strokes(app, ids[0], StrokeLayer.USER)] == [
        ("erase", 4.0, ((0.01, 0.01),))]
    assert out["lineart_url"] == page_out(client, b["id"], p["id"])["lineart_url"]

    # 已經沒有缺口：200，不新增筆畫，也不退回
    make_passed(app, p["id"])
    r = client.post(url, json={"generation_id": ids[0]})
    assert r.status_code == 200 and r.json()["autofix_added"] == 0
    assert r.json()["status"] == "passed"
    assert len(layer_strokes(app, ids[0], StrokeLayer.AUTO)) == 1

    # 使用者之後的修線只改 user 層，補線（auto 層）還在、線稿仍沒有缺口
    r = client.put(edits, json=edits_body(ids[0], []))
    assert r.status_code == 200 and r.json()["gap_count"] == 0
    assert client.get(edits).json()["strokes"] == []
    assert len(layer_strokes(app, ids[0], StrokeLayer.AUTO)) == 1


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
    assert client.get(f"/api/pages/{p['id']}/lineart/edits").json()["strokes"] == []
    strokes = layer_strokes(app, ids[0], StrokeLayer.AUTO)
    assert len(strokes) == added
    assert all(s.op == "draw" and s.width_px == 11 for s in strokes)


def test_autofix_respects_edit_layer_limits(client, app, monkeypatch):
    from app.services import page_lineart

    _, p, ids = selected_page(client, app)
    monkeypatch.setattr(page_lineart, "evaluate", lambda ink, lw: _fake_report(3))
    full = [Stroke("erase", 4.0, ((0.01, 0.01),))] * 500
    with app.state.sessions() as s:  # auto 層已滿（上限是每一層各自的）
        page_lineart.save_strokes(s, p["id"], ids[0], full, layer=StrokeLayer.AUTO)
        s.commit()
    r = client.post(f"/api/pages/{p['id']}/lineart/autofix", json={"generation_id": ids[0]})
    assert r.status_code == 409 and "上限" in r.json()["detail"]
    assert len(layer_strokes(app, ids[0], StrokeLayer.AUTO)) == 500


def test_autofix_limit_ignores_full_user_layer(client, app, monkeypatch):
    from app.api import lineart as lineart_api
    from app.services import page_lineart

    _, p, ids = selected_page(client, app)
    # user 層已滿（500 筆）；存檔後的線稿還有一個缺口，補一筆就接好
    reports = iter([_fake_report(1)])
    monkeypatch.setattr(page_lineart, "evaluate", lambda ink, lw: next(reports, _fake_report(0)))
    monkeypatch.setattr(lineart_api, "evaluate", lambda ink, lw: _fake_report(0))
    full = [{"op": "erase", "width_px": 4, "points": [[0.01, 0.01]]}] * 500
    assert client.put(f"/api/pages/{p['id']}/lineart/edits", json=edits_body(ids[0], full)).status_code == 200
    r = client.post(f"/api/pages/{p['id']}/lineart/autofix", json={"generation_id": ids[0]})
    assert r.status_code == 200, r.text
    assert r.json()["autofix_added"] == 1
    assert len(layer_strokes(app, ids[0], StrokeLayer.USER)) == 500
    assert len(layer_strokes(app, ids[0], StrokeLayer.AUTO)) == 1


@pytest.mark.parametrize("erase_px", [24, 30, 40])
def test_autofix_respects_user_eraser(client, app, erase_px):
    """使用者用橡皮擦擦開外框（1 個缺口）再按自動補線：渲染是 auto 再 user，所以被擦掉的補線不算。
    一次就接好，或什麼都不補（回 0）；再按一次不再新增 auto 筆畫，也不退回 passed。"""
    _, p, ids = selected_page(client, app)
    edits = f"/api/pages/{p['id']}/lineart/edits"
    url = f"/api/pages/{p['id']}/lineart/autofix"
    for y in (0.170, 0.172, 0.174, 0.176):  # 外框上緣附近：找一個會擦出缺口的位置
        erase = {"op": "erase", "width_px": erase_px, "points": [[0.5, y]]}
        r = client.put(edits, json=edits_body(ids[0], [erase]))
        assert r.status_code == 200, r.text
        if r.json()["gap_count"]:
            break
    assert r.json()["gap_count"] == 1

    out = client.post(url, json={"generation_id": ids[0]}).json()
    if out["autofix_added"]:
        assert out["gap_count"] == 0
    else:
        assert out["gap_count"] == 1
    auto_after_first = layer_strokes(app, ids[0], StrokeLayer.AUTO)
    assert len(auto_after_first) == out["autofix_added"]

    make_passed(app, p["id"])
    again = client.post(url, json={"generation_id": ids[0]}).json()
    assert again["autofix_added"] == 0
    assert again["status"] == "passed"
    assert layer_strokes(app, ids[0], StrokeLayer.AUTO) == auto_after_first
    assert client.get(edits).json()["strokes"] == [erase]


def test_edits_api_writes_user_layer_only(client, app):
    _, p, ids = selected_page(client, app)
    url = f"/api/pages/{p['id']}/lineart/edits"
    auto = [Stroke("draw", 11.0, ((0.3, 0.3), (0.4, 0.4)))]
    with app.state.sessions() as s:
        page_lineart_mod().save_strokes(s, p["id"], ids[0], auto, layer=StrokeLayer.AUTO)
        s.commit()
    with_auto = client.get(f"/api/pages/{p['id']}/lineart").content
    # GET 只回 user 層（使用者自己的筆畫），auto 層不出現在畫布上可撤銷的筆畫裡
    assert client.get(url).json() == {"generation_id": ids[0], "strokes": []}
    r = client.put(url, json=edits_body(ids[0], [DRAW]))
    assert r.status_code == 200, r.text
    assert {(e.layer, e.generation_id) for e in edit_rows(app)} == {
        (StrokeLayer.AUTO, ids[0]), (StrokeLayer.USER, ids[0])}
    assert layer_strokes(app, ids[0], StrokeLayer.AUTO) == auto
    assert client.get(url).json() == {"generation_id": ids[0], "strokes": [DRAW]}
    # 線稿兩層都套用：清掉 user 層就回到只有 auto 層的線稿
    assert client.get(r.json()["lineart_url"]).content != with_auto
    r = client.put(url, json=edits_body(ids[0], []))
    assert client.get(r.json()["lineart_url"]).content == with_auto


def page_lineart_mod():
    from app.services import page_lineart

    return page_lineart


def layer_strokes(app, gen_id, layer):
    with app.state.sessions() as s:
        return page_lineart_mod().load_strokes(s, gen_id, layer=layer)


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
    p = new_page(client, b["id"], scene_prompt="公園")
    queued = client.post(f"/api/pages/{p['id']}/backgrounds").json()
    assert client.get(f"/api/generations/{queued['id']}/lineart-preview").status_code == 404
    run_jobs(client, app)
    assert png(client.get(f"/api/generations/{queued['id']}/lineart-preview")).size == im.size
    assert client.get("/api/generations/999/lineart-preview").status_code == 404


def test_page_out_aspect_and_no_sketch_url(client, app):
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
    assert "sketch_url" not in page_out(client, b["id"], p["id"])  # 構圖草圖已移除（0006）


def test_change_size_recomputes_and_demotes(client, app, monkeypatch):
    from app.services import page_lineart
    from app.services.layout import PaperSize, compute_layout

    b, p1, ids1 = selected_page(client, app)
    p2 = new_page(client, b["id"])
    g2 = seed_generation(app, b["id"], p2["id"], color_png())
    set_page(app, p2["id"], selected_generation_id=g2)

    # 第一頁有一筆修線（相對座標 y=0.05，在方塊外的空白處）
    mark = {"op": "draw", "width_px": 12, "points": [[0.2, 0.05], [0.8, 0.05]]}
    assert client.put(f"/api/pages/{p1['id']}/lineart/edits",
                      json=edits_body(ids1[0], [mark])).status_code == 200
    for pid in (p1["id"], p2["id"]):
        make_passed(app, pid)

    new_px = compute_layout(PaperSize.A5, PrintMode.PRINT_SHOP).content_px
    assert list(new_px) != b["layout"]["content_px"]
    real = page_lineart.evaluate

    def fake_evaluate(ink, line_width_px, *a, **kw):
        report = real(ink, line_width_px, *a, **kw)
        h, w = ink.shape
        if (w, h) == tuple(new_px) and ink[round(0.05 * (h - 1)), w // 2]:
            report.errors = ["gap"]  # 只有帶著修線的頁在新尺寸下出現缺口
        return report

    monkeypatch.setattr(page_lineart, "evaluate", fake_evaluate)
    r = client.patch(f"/api/books/{b['id']}", json={"print_mode": "print_shop"})
    assert r.status_code == 200 and r.json()["layout"]["content_px"] == list(new_px)

    assert get_page(app, p1["id"]).status == PageStatus.DRAFT
    assert get_page(app, p2["id"]).status == PageStatus.PASSED
    for pid in (p1["id"], p2["id"]):
        assert list(png(client.get(f"/api/pages/{pid}/lineart")).size) == list(new_px)
    # 修線以相對座標保存，不變；且落在新圖縮放後的位置
    assert client.get(f"/api/pages/{p1['id']}/lineart/edits").json()["strokes"] == [mark]
    im = png(client.get(f"/api/pages/{p1['id']}/lineart"))
    w, h = im.size
    assert im.getpixel((w // 2, round(0.05 * (h - 1)))) == 0
    assert im.getpixel((w // 2, round(0.12 * (h - 1)))) == 255


def test_qa_out_has_near_miss_count(client, app):
    _, page, _ = selected_page(client, app)
    r = client.get(f"/api/pages/{page['id']}/qa")
    assert r.status_code == 200
    assert isinstance(r.json()["near_miss_count"], int)
