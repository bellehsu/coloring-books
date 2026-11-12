"""每條帶 book／page／generation id 的路由都要檢查擁有者（Phase 10 Task 5，Review Focus 1）。

路由清單直接從 app.routes 讀：新增的路由自動納入。不帶 id 的路由只能是下面明列的 allow-list。
"""

import re
import sqlite3

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.models import GenKind
from app.services import auth
from tests.fake_engine import FakeEngine
from tests.test_api_lineart import seed_generation, selected_page

COOKIE = "cb_session"
OWNED_PARAMS = {"book_id", "page_id", "gen_id"}
# 不帶擁有物 id 的路由（仍需登入，除了 /i 與 /api/files）。新增路由時，要嘛帶 id，要嘛明確加到這裡。
NO_OWNED_ID = {
    ("GET", "/api/books"),
    ("GET", "/api/shared/books"),  # 分享列表（唯讀）：只回別人分享且已完成的書
    ("POST", "/api/books"),
    ("GET", "/api/system/queue"),
    ("GET", "/api/system/preflight"),
    ("GET", "/api/me"),  # 任何有效 session 看自己的名字與身分
    ("GET", "/api/admin/invites"),  # 管理者專用（非管理者 404，見 tests/test_admin_invites.py）
    ("POST", "/api/admin/invites"),
    ("GET", "/i/{token}"),  # 邀請連結本身就是憑證
    ("GET", "/api/invites/peek"),  # 切換身分確認頁：X-Invite-Token 就是憑證
    ("POST", "/api/invites/accept"),  # 同上（body 的 token）
    ("GET", "/api/files/{key:path}"),  # LocalStorage 的 HMAC 簽章就是憑證（短效）
}
# FastAPI 內建的 API 文件（不含任何資料）
FASTAPI_DOCS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
PUBLIC = {
    ("GET", "/i/{token}"), ("GET", "/api/files/{key:path}"),
    ("GET", "/api/invites/peek"), ("POST", "/api/invites/accept"),
}
# 擁有者檢查用的 404 訊息（別人的資源與不存在的資源一樣）
NOT_FOUND = {"找不到這本繪本", "找不到這一頁", "找不到這筆生成紀錄"}
# 同一個 id 的其他寫法：pydantic 寬鬆模式把 "1.0"、"1.00"、" 1"、"+1"、"01" 都轉成 1
ID_SPELLINGS = ("{}.0", "{}.00", "%20{}", "{}%20", "%2B{}", "0{}")


def _routes():
    """(method, path, path 參數)；含 include_router 進來的路由（FastAPI 0.14x 起 app.routes 不再攤平，
    要用 iter_route_contexts 才看得到每一條實際生效的路由）。"""
    app = create_app()  # 只讀路由表，不跑 lifespan
    out = []
    for rc in iter_route_contexts(app.routes):
        route = rc.original_route
        if rc.path in FASTAPI_DOCS:
            continue
        assert isinstance(route, APIRoute), rc.path  # 其他種類的路由（mount、websocket）要另外想權限
        for m in sorted(route.methods - {"HEAD"}):
            out.append((m, rc.path, frozenset(re.findall(r"{(\w+)", rc.path))))
    return out


ROUTES = _routes()
# 分享的唯讀路由（/api/shared，Task 10）：不是擁有者路由，可見性規則與測試在 tests/test_sharing.py。
# 明列而不是用前綴排除：/api/shared 底下新增的路由會落到 ID_ROUTES（被當成擁有者路由測 404）或
# test_route_inventory_is_explicit（沒有 id 的路由），必須在這裡刻意分類。
SHARED = {
    ("GET", "/api/shared/books/{book_id}"),
    ("GET", "/api/shared/pages/{page_id}/image.png"),
    ("GET", "/api/shared/books/{book_id}/export.pdf"),
}
ID_ROUTES = [(m, p) for m, p, params in ROUTES if (m, p) not in NO_OWNED_ID | SHARED]
# Phase 13 新增的擁有者路由：明列，確認它們都在 ID_ROUTES（由下面的參數化測試逐一測 404）
PHASE13_ROUTES = {
    ("GET", "/api/books/{book_id}/produce/plan"),
    ("POST", "/api/books/{book_id}/produce"),
    ("GET", "/api/books/{book_id}/overview"),
    # Task 4：C 的復原／重做／還原，上傳自己的主角
    ("POST", "/api/pages/{page_id}/undo"),
    ("POST", "/api/pages/{page_id}/redo"),
    ("POST", "/api/pages/{page_id}/restore"),
    ("POST", "/api/books/{book_id}/character/upload"),
    # Task 7：C 的「這頁可以印了 ✓」
    ("POST", "/api/pages/{page_id}/accept"),
    # Task 7 fix round 1：C 的「再試一次」（只處理這一頁）
    ("POST", "/api/pages/{page_id}/retry"),
    # Phase 13b：C 的「把這個選擇套用到整本書」（圖下方的字要印哪幾行）
    ("POST", "/api/books/{book_id}/caption-display"),
}


def test_route_inventory_is_explicit():
    """不帶 id 的路由必須剛好是 allow-list；帶參數的路由只能用 book_id／page_id／gen_id。"""
    no_id = {(m, p) for m, p, params in ROUTES if not params & OWNED_PARAMS}
    assert no_id == NO_OWNED_ID
    for m, p, params in ROUTES:
        if (m, p) not in NO_OWNED_ID:
            assert params <= OWNED_PARAMS, (m, p, params)
    assert SHARED <= {(m, p) for m, p, _ in ROUTES}  # 清單裡的路由都真的存在
    assert len(ID_ROUTES) >= 35  # 防呆：路由表讀錯會變成空的參數化
    assert PHASE13_ROUTES <= set(ID_ROUTES)


@pytest.fixture
def dev_owner():
    return None


@pytest.fixture
def world(settings_file):
    """甲、乙兩個邀請連結＋一個 admin；甲有一本書（角色、一頁、選定圖、背景、角色動作）。"""
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False,
                     startup_preflight=False)
    with TestClient(app) as client:
        sids = {}
        with app.state.sessions() as s:
            tokens = {
                "a": auth.create_owner(s, "甲")[1],
                "b": auth.create_owner(s, "乙")[1],
                "admin": auth.create_owner(s, "管理者", is_admin=True)[1],
            }
        for who, token in tokens.items():
            client.cookies.clear()
            r = client.get(f"/i/{token}", follow_redirects=False)
            assert r.status_code == 303
            sids[who] = client.cookies[COOKIE]
        as_(client, sids["a"])
        b, p, ids = selected_page(client, app)
        from tests.test_api_lineart import color_png

        bg = seed_generation(app, b["id"], p["id"], color_png(), GenKind.BACKGROUND)
        ids = {"book_id": b["id"], "page_id": p["id"], "gen_id": ids[0], "bg": bg}
        yield app, client, sids, ids


def as_(client, sid):
    client.cookies.clear()
    if sid is not None:
        client.cookies.set(COOKIE, sid)


def _url(path, ids):
    return path.format(**{k: ids[k] for k in re.findall(r"{(\w+)", path)})


def _call(client, method, url):
    if method in ("POST", "PUT", "PATCH"):
        return client.request(method, url, json={}, follow_redirects=False)
    return client.request(method, url, follow_redirects=False)


def _snapshot(app) -> list[str]:
    """整個 DB 的內容（不含 owner_session：最後使用時間不算資料）＋所有存放的物件。"""
    con = sqlite3.connect(app.state.settings.paths.database)
    try:
        rows = [line for line in con.iterdump() if "owner_session" not in line]
    finally:
        con.close()
    st = app.state.storage
    return rows + sorted([*st.list("books/"), *st.list("cache/")])


@pytest.mark.parametrize("method,path", ID_ROUTES, ids=[f"{m} {p}" for m, p in ID_ROUTES])
def test_ownership_404_on_every_route(world, method, path):
    app, client, sids, ids = world
    url = _url(path, ids)
    before = _snapshot(app)
    as_(client, sids["b"])
    r = _call(client, method, url)
    assert r.status_code == 404, (url, r.status_code, r.text)
    assert r.json()["detail"] in NOT_FOUND
    assert "location" not in r.headers
    assert _snapshot(app) == before  # 別人的請求不能改到任何東西

    # 不存在的 id 也是同樣的 404（別人的資源看起來跟不存在一樣）
    missing = _url(path, {k: 987654 for k in OWNED_PARAMS})
    r2 = _call(client, method, missing)
    assert r2.status_code == 404 and r2.json()["detail"] == r.json()["detail"]

    # 換一種寫法的 id（FastAPI／pydantic 寬鬆模式會轉成同一個整數）也不能繞過檢查（Task 5 review C1）
    for fmt in ID_SPELLINGS:
        alt = _url(path, {k: fmt.format(v) for k, v in ids.items()})
        r3 = _call(client, method, alt)
        assert r3.status_code == 404, (alt, r3.status_code, r3.text)
        assert "location" not in r3.headers, alt
        assert _snapshot(app) == before, alt

    # admin 看得到全部；擁有者本人當然也可以（DELETE 之後就沒了，只測 admin）
    for who in ("admin",) if method == "DELETE" else ("admin", "a"):
        as_(client, sids[who])
        r = _call(client, method, url)
        assert r.status_code not in (401, 403), (who, url, r.text)
        if r.status_code == 404:
            assert r.json()["detail"] not in NOT_FOUND, (who, url, r.text)


def test_endpoints_load_ids_through_owner_scoped_helpers():
    """縱深防禦：除了路由層級的檢查，每個帶 id 的端點本身也拿 current_owner，
    且 API 模組裡沒有不分擁有者就載入 book／page／generation 的輔助函式。"""
    import inspect
    from pathlib import Path

    from app.api.deps import current_owner

    app = create_app()
    for rc in iter_route_contexts(app.routes):
        route = rc.original_route
        if not isinstance(route, APIRoute) or not set(re.findall(r"{(\w+)", rc.path)) & OWNED_PARAMS:
            continue
        deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
                if hasattr(p.default, "dependency")]
        assert current_owner in deps, rc.path
    api_dir = Path(__file__).resolve().parents[1] / "backend" / "app" / "api"
    unscoped = re.compile(
        r"_book_or_404|_page_or_404|_gen_or_404|\.get\((Book|Page|Generation),\s*(book_id|page_id|gen_id)\b"
    )
    offenders = [f"{f.name}:{i}" for f in sorted(api_dir.glob("*.py")) if f.name != "deps.py"
                 for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if unscoped.search(line)]
    assert offenders == []


@pytest.mark.parametrize("method,path", [(m, p) for m, p, _ in ROUTES if (m, p) not in PUBLIC])
def test_every_route_requires_session(world, method, path):
    app, client, sids, ids = world
    as_(client, None)
    r = _call(client, method, _url(path, ids))
    assert r.status_code == 401 and r.json()["detail"] == "請用邀請連結開啟", (path, r.text)


def test_admin_gets_200(world):
    app, client, sids, ids = world
    as_(client, sids["admin"])
    b, p, g = ids["book_id"], ids["page_id"], ids["gen_id"]
    for url in (f"/api/books/{b}", f"/api/books/{b}/pages", f"/api/books/{b}/generations",
                f"/api/generations/{g}", f"/api/pages/{p}/qa", f"/api/books/{b}/export/summary",
                f"/api/books/{b}/produce/plan", f"/api/books/{b}/overview"):
        assert client.get(url).status_code == 200, url
    for url in (f"/api/generations/{g}/image", f"/api/pages/{p}/lineart"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 302, url
        assert client.get(r.headers["location"]).status_code == 200
    r = client.patch(f"/api/books/{b}", json={"title": "admin 改的"})
    assert r.status_code == 200 and r.json()["title"] == "admin 改的"


def test_body_generation_id_of_other_owner_is_404(world):
    """body 裡指到別人的 generation：與不存在一樣回 404，不透露它存在。"""
    app, client, sids, ids = world
    as_(client, sids["b"])
    mine = client.post("/api/books", json={"title": "乙", "paper_size": "a5", "print_mode": "home"}).json()
    page = client.post(f"/api/books/{mine['id']}/pages", json={}).json()
    r = client.post(f"/api/books/{mine['id']}/character", json={"generation_id": ids["gen_id"]})
    assert r.status_code == 404 and r.json()["detail"] == "找不到這筆生成紀錄"
    r = client.post(f"/api/pages/{page['id']}/background", json={"generation_id": ids["bg"]})
    assert r.status_code == 404 and r.json()["detail"] == "找不到這筆生成紀錄"


# 超出 SQLite INTEGER（64 位元有號）的 id：以前 session.get 丟 OverflowError → 500（final review M1）
HUGE_IDS = (2**63, 2**64, 10**23, -(2**63) - 1)


@pytest.mark.parametrize("method,path", ID_ROUTES, ids=[f"{m} {p}" for m, p in ID_ROUTES])
def test_huge_path_ids_are_404_not_500(world, method, path):
    app, client, sids, ids = world
    as_(client, sids["admin"])  # admin 通過擁有權檢查，會一路走到查 DB
    before = _snapshot(app)
    for huge in HUGE_IDS:
        r = _call(client, method, _url(path, {k: huge for k in OWNED_PARAMS}))
        assert r.status_code == 404, (path, huge, r.status_code, r.text)
        assert r.json()["detail"] in NOT_FOUND
    assert _snapshot(app) == before


def test_huge_body_and_query_ids_are_422_not_500(world):
    app, client, sids, ids = world
    as_(client, sids["admin"])
    b, p = ids["book_id"], ids["page_id"]
    for huge in (2**63, -(2**63) - 1):
        cases = [
            ("POST", f"/api/books/{b}/character", {"generation_id": huge}),
            ("POST", f"/api/pages/{p}/background", {"generation_id": huge}),
            ("POST", f"/api/pages/{p}/pose", {"generation_id": huge}),
            ("POST", f"/api/books/{b}/pages/order", {"page_ids": [p, huge]}),
            ("POST", f"/api/books/{b}/materials/batch", {"upload_page_ids": [huge]}),
            ("PUT", f"/api/pages/{p}/lineart/edits", {"generation_id": huge, "strokes": []}),
            ("POST", f"/api/pages/{p}/lineart/autofix", {"generation_id": huge}),
            ("GET", f"/api/books/{b}/generations?page_id={huge}", None),
        ]
        for method, url, body in cases:
            r = client.request(method, url, json=body)
            assert r.status_code == 422, (method, url, huge, r.status_code, r.text)
    # 範圍內的照常（邊界值本身合法，查不到是 404 不是 422）
    r = client.post(f"/api/pages/{p}/background", json={"generation_id": 2**63 - 1})
    assert r.status_code == 404


@pytest.mark.parametrize("method,path", sorted(SHARED), ids=[f"{m} {p}" for m, p in sorted(SHARED)])
def test_huge_path_ids_are_404_not_500_on_shared_routes(world, method, path):
    app, client, sids, ids = world
    as_(client, sids["b"])
    for huge in HUGE_IDS:
        r = _call(client, method, _url(path, {k: huge for k in OWNED_PARAMS}))
        assert r.status_code == 404, (path, huge, r.status_code, r.text)


def test_caption_patch_by_other_owner_is_404(world):
    app, client, sids, ids = world
    as_(client, sids["b"])
    r = client.patch(f"/api/pages/{ids['page_id']}", json={"caption_zh": "消防車"})
    assert r.status_code == 404 and r.json()["detail"] in NOT_FOUND
    as_(client, sids["a"])
    assert client.get(f"/api/books/{ids['book_id']}/pages").json()[0]["caption_zh"] is None
