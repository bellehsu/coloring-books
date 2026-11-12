"""scripts/sync_comfy_model_paths.py 與 scripts/check_env.py。"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sync = _load("sync_comfy_model_paths")
check_env = _load("check_env")

REQUIRED = {"checkpoints", "loras", "controlnet", "ipadapter", "clip_vision", "unet",
            "diffusion_models", "text_encoders", "vae"}


def test_build_section_has_required_folders(tmp_path):
    sec = sync.build_section(tmp_path)
    assert sec["base_path"] == str(tmp_path)
    assert REQUIRED <= set(sec)


def test_build_section_maps_background_removal(tmp_path):
    # Qwen 角色動作的 BiRefNet 去背模型放在 models_root/background_removal/
    sec = sync.build_section(tmp_path)
    assert sec["background_removal"] == "background_removal"


def test_cli_create_dirs_creates_background_removal(settings_file, tmp_path):
    out = tmp_path / "extra.yaml"
    assert sync.main(["--settings", str(settings_file), "--output", str(out), "--create-dirs"]) == 0
    assert (tmp_path / "models" / "background_removal").is_dir()
    assert yaml.safe_load(out.read_text())["coloring_books"]["background_removal"] == "background_removal"


def test_merge_keeps_other_sections_and_warns_on_same_base():
    existing = "ai_models:\n  base_path: /m\n  checkpoints: checkpoints\nother:\n  base_path: /x\n"
    text, warnings = sync.merge_into(existing, "coloring_books", sync.build_section(Path("/m")))
    data = yaml.safe_load(text)
    assert data["ai_models"]["checkpoints"] == "checkpoints"
    assert data["other"]["base_path"] == "/x"
    assert data["coloring_books"]["base_path"] == "/m"
    assert len(warnings) == 1 and "ai_models" in warnings[0]


def test_merge_replaces_own_section():
    existing = "coloring_books:\n  base_path: /old\n  stale: stale\n"
    text, _ = sync.merge_into(existing, "coloring_books", sync.build_section(Path("/new")))
    data = yaml.safe_load(text)
    assert data["coloring_books"]["base_path"] == "/new"
    assert "stale" not in data["coloring_books"]


def test_merge_rejects_non_mapping():
    with pytest.raises(ValueError):
        sync.merge_into("- a\n- b\n", "coloring_books", sync.build_section(Path("/m")))


def test_cli_dry_run_writes_nothing(settings_file, tmp_path, capsys):
    out = tmp_path / "extra.yaml"
    assert sync.main(["--settings", str(settings_file), "--output", str(out), "--dry-run"]) == 0
    assert not out.exists()
    printed = capsys.readouterr().out
    assert "coloring_books:" in printed and "ipadapter: ipadapter" in printed


def test_cli_output_backup_and_create_dirs(settings_file, tmp_path):
    out = tmp_path / "comfy" / "extra.yaml"
    out.parent.mkdir()
    out.write_text("mine:\n  base_path: /keep\n")
    assert sync.main(["--settings", str(settings_file), "--output", str(out), "--create-dirs"]) == 0
    data = yaml.safe_load(out.read_text())
    assert data["mine"]["base_path"] == "/keep"
    assert data["coloring_books"]["base_path"] == str(tmp_path / "models")
    assert (out.parent / "extra.yaml.bak").read_text() == "mine:\n  base_path: /keep\n"
    assert all((tmp_path / "models" / sub).is_dir() for sub in REQUIRED)


def test_cli_install_dir_requires_setting(settings_file):
    assert sync.main(["--settings", str(settings_file), "--install-dir"]) == 2


def test_cli_install_dir_mode(settings_file, tmp_path):
    install = tmp_path / "ComfyUI"
    install.mkdir()
    (install / "main.py").write_text("")
    data = yaml.safe_load(settings_file.read_text())
    data["comfy"]["install_dir"] = str(install)
    settings_file.write_text(yaml.safe_dump(data))
    assert sync.main(["--settings", str(settings_file), "--install-dir"]) == 0
    assert "coloring_books" in yaml.safe_load((install / "extra_model_paths.yaml").read_text())


# ---- check_env ------------------------------------------------------------------

def test_check_comfy_unreachable():
    r = check_env.Report()
    check_env.check_comfy(r, "http://127.0.0.1:1", timeout=0.5)
    assert r.failed and "無法連線" in r.items[0]["detail"]


def test_check_comfy_parses_system_stats(monkeypatch):
    stats = {"system": {"comfyui_version": "0.36.0", "pytorch_version": "2.14.0+cu130"},
             "devices": [{"type": "cuda", "name": "RTX 5080", "vram_total": 17094475776}]}
    monkeypatch.setattr(check_env, "_get_json", lambda url, timeout: stats)
    r = check_env.Report()
    check_env.check_comfy(r, "http://x", timeout=1)
    assert not r.failed
    assert any("cu" in i["detail"] and i["ok"] for i in r.items if i["name"] == "ComfyUI torch")


def test_check_comfy_old_cuda_fails(monkeypatch):
    stats = {"system": {"pytorch_version": "2.3.0+cu121"}, "devices": [{"type": "cuda", "vram_total": 17e9}]}
    monkeypatch.setattr(check_env, "_get_json", lambda url, timeout: stats)
    r = check_env.Report()
    check_env.check_comfy(r, "http://x", timeout=1)
    assert r.failed


def test_check_comfy_rejects_non_comfy(monkeypatch):
    monkeypatch.setattr(check_env, "_get_json", lambda url, timeout: {"hello": 1})
    r = check_env.Report()
    check_env.check_comfy(r, "http://x", timeout=1)
    assert r.failed and "不是 ComfyUI" in r.items[0]["detail"]


def test_comfy_url_from_settings(settings_file, monkeypatch):
    monkeypatch.delenv("APP_COMFY__URL", raising=False)
    assert check_env.comfy_url_from_settings(settings_file) == "http://comfy.test:8188"
    monkeypatch.setenv("APP_COMFY__URL", "http://env:1")
    assert check_env.comfy_url_from_settings(settings_file) == "http://env:1"


def test_export_openapi_writes_paths(tmp_path, settings_file, monkeypatch):
    import json

    monkeypatch.setenv("APP_SETTINGS_FILE", str(settings_file))
    mod = _load("export_openapi")
    out = tmp_path / "deep" / "dir" / "openapi.json"
    monkeypatch.setattr(sys, "argv", ["export_openapi.py", str(out)])
    assert mod.main() == 0
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert "/api/pages/{page_id}/lineart/edits" in spec["paths"]
    assert "delete" in spec["paths"]["/api/pages/{page_id}"]


# ---- dev_fake_backend --------------------------------------------------------

fake_backend = _load("dev_fake_backend")


def _wait_succeeded(client, book_id: int, kind: str, n: int, timeout_s: float = 10.0) -> list[dict]:
    import time

    deadline = time.monotonic() + timeout_s
    while True:
        gens = client.get(f"/api/books/{book_id}/generations", params={"kind": kind}).json()
        if len(gens) >= n and all(g["status"] == "succeeded" for g in gens):
            return gens
        assert time.monotonic() < deadline, [g["status"] for g in gens]
        time.sleep(0.05)


@pytest.mark.parametrize(("paper", "mode"), [("a4", "print_shop"), ("a4", "home"), ("a5", "home"),
                                             ("letter", "print_shop"), ("square_21", "home")])
def test_demo_image_has_one_leak_that_autofix_closes(paper, mode):
    """DemoEngine 的圖經真正的線稿流程（render_base → evaluate）要有會漏色的缺口，補上 bridges 後歸零。"""
    from app.services.layout import PaperSize, PrintMode, compute_layout
    from app.services.lineart_render import LINE_WIDTH_DEFAULT, Stroke, apply_strokes, render_base
    from app.services.qa import evaluate

    import numpy as np

    lay = compute_layout(PaperSize(paper), PrintMode(mode))
    img = np.asarray(fake_backend.draw_demo(*lay.generation_size, seed=3))
    ink, _ = render_base(img, lay.content_px, "standard", LINE_WIDTH_DEFAULT)
    report = evaluate(ink, LINE_WIDTH_DEFAULT)
    assert report.gap_count >= 1 and "gaps" in report.errors
    # 缺口就在大框上緣中央
    fx0, fy0, fx1, _ = fake_backend.GAP_FRAME
    assert any(abs(x - (fx0 + fx1) / 2) < 0.05 and abs(y - fy0) < 0.05 for x, y in report.gaps)
    fixed = apply_strokes(ink, [Stroke("draw", LINE_WIDTH_DEFAULT, b) for b in report.bridges])
    assert evaluate(fixed, LINE_WIDTH_DEFAULT).gap_count == 0


@pytest.mark.parametrize(("paper", "mode"), [("a4", "print_shop"), ("a4", "home"), ("a5", "home"),
                                             ("letter", "print_shop"), ("square_21", "home")])
@pytest.mark.parametrize("placement", ["default", "e2e", "moved_left", "flipped"])
def test_demo_composite_has_one_leak_that_autofix_closes(paper, mode, placement):
    """新流程：背景＋角色動作經 compose()（預設位置與 e2e 移動後的位置）→ 真正的線稿流程，
    仍要有背景框上的那個缺口（角色沒蓋到它、也沒多出別的缺口），補上 bridges 後歸零。"""
    from dataclasses import replace

    import numpy as np

    from app.services.compose import DEFAULT_PLACEMENT, compose
    from app.services.layout import PaperSize, PrintMode, compute_layout
    from app.services.lineart_render import LINE_WIDTH_DEFAULT, Stroke, apply_strokes, render_base
    from app.services.qa import evaluate

    pl = {"default": DEFAULT_PLACEMENT,
          "e2e": replace(DEFAULT_PLACEMENT, cx=0.47, cy=0.68),  # flow.spec.ts 拖曳＋方向鍵後的位置
          "moved_left": replace(DEFAULT_PLACEMENT, cx=DEFAULT_PLACEMENT.cx - 0.05),
          "flipped": replace(DEFAULT_PLACEMENT, flip=True)}[placement]
    lay = compute_layout(PaperSize(paper), PrintMode(mode))
    bg = fake_backend.draw_background(*lay.generation_size, seed=3)
    assert bg.size == tuple(lay.generation_size)
    pose, mask = fake_backend.draw_pose(seed=1)
    out = compose(bg, pose, mask, pl)
    ink, _ = render_base(np.asarray(out), lay.content_px, "standard", LINE_WIDTH_DEFAULT)
    report = evaluate(ink, LINE_WIDTH_DEFAULT)
    assert report.gap_count == 1 and "gaps" in report.errors
    fx0, fy0, fx1, _ = fake_backend.BG_GAP_FRAME
    (x, y), = report.gaps
    assert abs(x - (fx0 + fx1) / 2) < 0.05 and abs(y - fy0) < 0.05
    fixed = apply_strokes(ink, [Stroke("draw", LINE_WIDTH_DEFAULT, b) for b in report.bridges])
    assert evaluate(fixed, LINE_WIDTH_DEFAULT).gap_count == 0


def test_demo_pose_mask_matches_character():
    """角色動作：固定 POSE_SIZE、白底；遮罩同尺寸，角色（含黑色外框）處白、其他黑。"""
    import numpy as np

    from app.services.prompts import POSE_SIZE

    pose, mask = fake_backend.draw_pose(seed=2)
    assert pose.size == mask.size == POSE_SIZE and mask.mode == "L"
    rgb = np.asarray(pose).astype(int)
    m = np.asarray(mask)
    assert set(np.unique(m)) <= {0, 255} and (m == 255).any()
    non_white = (rgb < 250).any(axis=2)
    # 遮罩外全是白底；角色的每個非白像素（填色與外框）都在遮罩內
    assert not non_white[m == 0].any()
    assert non_white[m == 255].mean() > 0.99


def test_demo_engine_reports_progress_and_size():
    import asyncio
    import io

    from PIL import Image

    from app.models import GenKind
    from app.services.engine import GenerationRequest

    steps: list[tuple[int, int]] = []
    req = GenerationRequest(kind=GenKind.PAGE, prompt="p", seed=5, width=864, height=1216)
    res = asyncio.run(fake_backend.DemoEngine(delay_s=0.03).generate(req, lambda v, m: steps.append((v, m))))
    assert steps == [(1, 3), (2, 3), (3, 3)]
    assert Image.open(io.BytesIO(res.image)).size == (864, 1216)
    assert res.engine == "demo" and res.params["seed"] == 5
    assert res.extra_images == {}


@pytest.mark.parametrize(("kind", "size", "has_mask"), [
    ("background", (1024, 1024), False),
    ("pose", (832, 1216), True),
    ("character", (1024, 1024), False),
])
def test_demo_engine_kinds(kind, size, has_mask):
    """background 依請求尺寸；pose 固定 832×1216 並附同尺寸遮罩（worker 存成 mask_path）。"""
    import asyncio
    import io

    from PIL import Image

    from app.models import GenKind
    from app.services.engine import GenerationRequest

    req = GenerationRequest(kind=GenKind(kind), prompt="p", seed=1, width=size[0], height=size[1])
    res = asyncio.run(fake_backend.DemoEngine(delay_s=0).generate(req))
    assert Image.open(io.BytesIO(res.image)).size == size
    assert set(res.extra_images) == ({"mask"} if has_mask else set())
    if has_mask:
        assert Image.open(io.BytesIO(res.extra_images["mask"])).size == size
    assert res.params["kind"] == kind


def test_demo_engine_pose_records_variation():
    """DemoEngine 的角色動作回傳固定 832×1216 的角色圖與同尺寸遮罩；有變化語就記在 params（同 ComfyEngine）。"""
    import asyncio
    import io

    from PIL import Image

    from app.models import GenKind
    from app.services.engine import GenerationRequest

    req = GenerationRequest(kind=GenKind.POSE, prompt="p", seed=1, width=832, height=1216,
                            variation="facing left")
    res = asyncio.run(fake_backend.DemoEngine(delay_s=0).generate(req))
    img = Image.open(io.BytesIO(res.image))
    assert img.size == Image.open(io.BytesIO(res.extra_images["mask"])).size == (832, 1216)
    assert res.params["variation"] == "facing left"
    assert not {"pose_mode", "structure_strength", "has_structure_image"} & set(res.params)
    req.variation = None
    assert "variation" not in asyncio.run(fake_backend.DemoEngine(delay_s=0).generate(req)).params


def test_fake_backend_test_invite_route(settings_file, tmp_path):
    """假後端專用的 /api/_test/invite：建立第二個邀請，/i/{token} 之後只看得到自己的書；真 app 沒有這個路由。"""
    from fastapi.testclient import TestClient

    from app.config import load_settings
    from app.main import create_app

    app = fake_backend.create_demo_app(tmp_path / "s", load_settings(settings_file),
                                       engine=fake_backend.DemoEngine(delay_s=0.01))
    with TestClient(app) as admin:
        admin.post("/api/books", json={"title": "A", "paper_size": "a4", "print_mode": "print_shop"})
        res = admin.post("/api/_test/invite", json={"name": "B"})
        assert res.status_code == 200
        token = res.json()["token"]
        assert len(admin.get("/api/books").json()) == 1
        with TestClient(app) as b:
            assert b.get(f"/i/{token}", follow_redirects=False).status_code in (302, 303, 307)
            assert b.get("/api/books").json() == []
    real = create_app(load_settings(settings_file), start_worker=False, startup_preflight=False)
    assert "/api/_test/invite" not in set(real.openapi()["paths"])


def _wait_page(c, book_id, page_id, pred, timeout_s=20):
    deadline = time.monotonic() + timeout_s
    while True:
        page = next(p for p in c.get(f"/api/books/{book_id}/pages").json() if p["id"] == page_id)
        if pred(page):
            return page
        assert time.monotonic() < deadline, page
        time.sleep(0.05)


def test_fake_backend_full_flow(settings_file, tmp_path, monkeypatch):
    """假後端可啟動：角色 → 背景 → 角色動作（有遮罩、去背圖）→ 合成 → QA 有缺口 → 自動補線後 0；
    資料寫在 --storage，不碰設定的 storage。"""
    import httpx
    from fastapi.testclient import TestClient

    from app import main as app_main
    from app.config import load_settings
    from app.services.comfy_client import ComfyClient

    def refused(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(app_main, "ComfyClient",
                        lambda url, **kw: ComfyClient(url, transport=httpx.MockTransport(refused), **kw))
    storage = tmp_path / "fake-storage"
    app = fake_backend.create_demo_app(storage, load_settings(settings_file),
                                       engine=fake_backend.DemoEngine(delay_s=0.02))
    with TestClient(app) as c:
        book = c.post("/api/books", json={"title": "示範", "paper_size": "a4", "print_mode": "print_shop"}).json()
        assert c.post(f"/api/books/{book['id']}/character/candidates",
                      json={"description": "a cat"}).status_code == 202
        chars = _wait_succeeded(c, book["id"], "character", 1)
        assert c.post(f"/api/books/{book['id']}/character", json={"generation_id": chars[0]["id"]}).status_code == 200

        page = c.post(f"/api/books/{book['id']}/pages", json={"scene_prompt": "park"}).json()
        assert c.post(f"/api/pages/{page['id']}/backgrounds").status_code == 202
        bg = _wait_succeeded(c, book["id"], "background", 1)[0]
        assert (bg["width"], bg["height"]) == (864, 1216)
        assert c.post(f"/api/pages/{page['id']}/background", json={"generation_id": bg["id"]}).status_code == 200

        assert c.post(f"/api/pages/{page['id']}/poses", json={"action": "waving one hand"}).status_code == 202
        pose = _wait_succeeded(c, book["id"], "pose", 1)[0]
        assert (pose["width"], pose["height"]) == (832, 1216)
        assert pose["cutout_url"]
        assert c.get(pose["cutout_url"]).status_code == 200
        assert c.post(f"/api/pages/{page['id']}/pose", json={"generation_id": pose["id"]}).status_code == 200

        # 頁面流程（Phase 13）：背景與角色動作都好了 → 自動合成、補線、可以印
        done = _wait_page(c, book["id"], page["id"], lambda p: p["status"] == "passed")
        assert done["selected_generation_id"] is not None and done["compose_pending"] is False
        assert c.get(f"/api/pages/{page['id']}/qa").json()["gap_count"] == 0

        # 舊編輯器：拖了角色再確認合成 → 新的合成圖（退回草稿）→ 自動補線
        assert c.put(f"/api/pages/{page['id']}/placement",
                     json={"cx": 0.45, "cy": 0.66, "height": 0.6, "flip": False}).status_code == 200
        composed = c.post(f"/api/pages/{page['id']}/compose")
        assert composed.status_code == 200, composed.text
        sel = composed.json()["selected_generation_id"]
        assert sel is not None

        qa = c.get(f"/api/pages/{page['id']}/qa").json()
        assert qa["gap_count"] == 1
        fixed = c.post(f"/api/pages/{page['id']}/lineart/autofix", json={"generation_id": sel}).json()
        assert fixed["autofix_added"] >= 1 and fixed["gap_count"] == 0
    assert (storage / "app.db").is_file()
    assert not (tmp_path / "storage" / "app.db").exists()


def test_fake_backend_story_flow(settings_file, tmp_path, monkeypatch):
    """e2e 用到的新流程經假後端都能跑：中文 action_prompt → 產生全部素材（上傳頁略過背景）→
    worker 自動選用 → 合成 → 補線前就按完成（有缺口、不填原因，後端自動補原因）；上傳背景圖裁成頁面比例。"""
    import io
    import time

    import httpx
    from fastapi.testclient import TestClient
    from PIL import Image

    from app import main as app_main
    from app.config import load_settings
    from app.services.comfy_client import ComfyClient

    def refused(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(app_main, "ComfyClient",
                        lambda url, **kw: ComfyClient(url, transport=httpx.MockTransport(refused), **kw))
    app = fake_backend.create_demo_app(tmp_path / "s", load_settings(settings_file),
                                       engine=fake_backend.DemoEngine(delay_s=0.02))
    with TestClient(app) as c:
        book = c.post("/api/books", json={"title": "故事", "paper_size": "a4", "print_mode": "print_shop"}).json()
        c.post(f"/api/books/{book['id']}/character/candidates", json={"description": "一隻圓圓的小貓"})
        char = _wait_succeeded(c, book["id"], "character", 1)[0]
        c.post(f"/api/books/{book['id']}/character", json={"generation_id": char["id"]})
        p1 = c.post(f"/api/books/{book['id']}/pages",
                    json={"scene_prompt": "陽光公園", "action_prompt": "抱著球開心地跳"}).json()
        p2 = c.post(f"/api/books/{book['id']}/pages", json={"scene_prompt": "海邊", "action_prompt": "看書"}).json()

        res = c.post(f"/api/books/{book['id']}/materials/batch", json={"upload_page_ids": [p2["id"]]})
        assert res.status_code == 202, res.text
        assert [(q["page_id"], q["kind"]) for q in res.json()["queued"]] == [
            (p1["id"], "background"), (p1["id"], "pose"), (p2["id"], "pose")]
        poses = _wait_succeeded(c, book["id"], "pose", 2)
        assert {g["user_text"] for g in poses} == {"抱著球開心地跳", "看書"}
        _wait_succeeded(c, book["id"], "background", 1)

        # 素材自動選用後，頁面流程（Phase 13）自動合成、補線並標成可以印
        done = _wait_page(c, book["id"], p1["id"], lambda p: p["status"] == "passed")
        assert done["background_generation_id"] and done["pose_generation_id"]
        assert done["compose_pending"] is False and done["selected_generation_id"] is not None

        # 舊編輯器照舊：換位置再確認合成 → 草稿、有缺口 → 「對」→ 完成這一頁
        assert c.put(f"/api/pages/{p1['id']}/placement",
                     json={"cx": 0.45, "cy": 0.66, "height": 0.6, "flip": False}).status_code == 200
        composed = c.post(f"/api/pages/{p1['id']}/compose").json()
        assert composed["status"] == "draft"
        sel = composed["selected_generation_id"]
        assert c.get(f"/api/pages/{p1['id']}/qa").json()["gap_count"] == 1
        c.post(f"/api/pages/{p1['id']}/character-check", json={"generation_id": sel, "result": "consistent"})
        passed = c.post(f"/api/pages/{p1['id']}/pass", json={})
        assert passed.status_code == 200, passed.text
        assert passed.json()["status"] == "passed"
        assert c.get(f"/api/pages/{p1['id']}/qa").json()["forced_pass_reason"] == "使用者確認完成（1 個缺口）"

        buf = io.BytesIO()
        Image.new("L", (4000, 800), 200).save(buf, "PNG")
        up = c.post(f"/api/pages/{p2['id']}/backgrounds/upload", files={"file": ("wide.png", buf.getvalue(), "image/png")})
        assert up.status_code == 201, up.text
        bg = c.get(f"/api/generations/{up.json()['background_generation_id']}").json()
        assert bg["source"] == "upload" and (bg["width"], bg["height"]) == (864, 1216)


def test_fake_backend_ignores_local_comfyui(settings_file, tmp_path, monkeypatch):
    """本機 ComfyUI 連得上、但 workflow 都跑不了（/object_info 空的）：假後端啟動時不跑 preflight、不連 ComfyUI，
    角色動作照常生成（e2e 不受本機 ComfyUI 狀態影響）。"""
    import httpx
    from fastapi.testclient import TestClient

    from app import main as app_main
    from app.config import load_settings
    from app.services.comfy_client import ComfyClient

    seen: list[str] = []

    def comfy(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.36"}, "devices": []})
        return httpx.Response(200, json={} if request.url.path == "/object_info" else [])

    monkeypatch.setattr(app_main, "ComfyClient",
                        lambda url, **kw: ComfyClient(url, transport=httpx.MockTransport(comfy), **kw))
    app = fake_backend.create_demo_app(tmp_path / "s", load_settings(settings_file),
                                       engine=fake_backend.DemoEngine(delay_s=0.02))
    with TestClient(app) as c:
        book = c.post("/api/books", json={"title": "示範", "paper_size": "a4", "print_mode": "print_shop"}).json()
        c.post(f"/api/books/{book['id']}/character/candidates", json={"description": "a cat", "count": 1})
        char = _wait_succeeded(c, book["id"], "character", 1)[0]
        c.post(f"/api/books/{book['id']}/character", json={"generation_id": char["id"]})
        page = c.post(f"/api/books/{book['id']}/pages", json={"scene_prompt": "park"}).json()
        res = c.post(f"/api/pages/{page['id']}/poses", json={"action": "waving one hand"})
        assert res.status_code == 202, res.text
        assert _wait_succeeded(c, book["id"], "pose", 1)[0]["kind"] == "pose"
    assert seen == []
    # 對照：同一個 ComfyUI 下真的跑 preflight，workflow 會被判定不能用（上面的結果不是巧合）
    app2 = app_main.create_app(fake_backend.demo_settings(tmp_path / "s2", load_settings(settings_file)),
                               engine=fake_backend.DemoEngine(delay_s=0), start_worker=False)
    with TestClient(app2):
        pass
    assert "/object_info" in seen


def test_fake_backend_always_uses_local_storage(settings_file, tmp_path):
    """設定檔寫 r2 時，假後端照樣用 LocalStorage（e2e 不需要網路或 R2 憑證）。"""
    from fastapi.testclient import TestClient

    from app.config import load_settings
    from app.storage import LocalStorage

    base = load_settings(settings_file)
    base = base.model_copy(update={"storage": base.storage.model_copy(
        update={"backend": "r2", "cache_dir": tmp_path / "r2cache"})})
    s = fake_backend.demo_settings(tmp_path / "s", base)
    assert s.storage.backend == "local" and s.storage.cache_dir is None
    app = fake_backend.create_demo_app(tmp_path / "s", base, engine=fake_backend.DemoEngine(delay_s=0))
    with TestClient(app):
        assert isinstance(app.state.storage, LocalStorage)
        assert app.state.storage.root == (tmp_path / "s").resolve()


def test_fake_backend_defaults_to_dev_owner_admin(settings_file, tmp_path):
    """假後端（e2e）沒有 cookie 也能用：設定檔沒設 auth.dev_owner 時，仍預設 admin。"""
    from fastapi.testclient import TestClient

    from app.config import load_settings

    base = load_settings(settings_file)
    base = base.model_copy(update={"auth": base.auth.model_copy(update={"dev_owner": None})})
    assert fake_backend.demo_settings(tmp_path / "s", base).auth.dev_owner == "admin"
    assert fake_backend.demo_settings(tmp_path / "s", base, dev_owner=None).auth.dev_owner is None
    app = fake_backend.create_demo_app(tmp_path / "s", base, engine=fake_backend.DemoEngine(delay_s=0))
    with TestClient(app) as c:
        assert c.get("/api/books").status_code == 200


def test_fake_backend_cli_runs_uvicorn_on_port(settings_file, tmp_path, monkeypatch):
    import uvicorn

    monkeypatch.setenv("APP_SETTINGS_FILE", str(settings_file))
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    storage = tmp_path / "s"
    assert fake_backend.main(["--port", "8123", "--storage", str(storage)]) == 0
    (app, kw), = calls
    assert kw["port"] == 8123 and kw["host"] == "127.0.0.1"
    assert storage.is_dir() and app.title == "Coloring Books"


# ---------- deploy/windows-portproxy.ps1（Windows PowerShell 5.1） ----------

PORTPROXY = REPO / "deploy" / "windows-portproxy.ps1"


def test_portproxy_script_has_utf8_bom():
    """PS 5.1 讀沒有 BOM 的檔案時用 ANSI 字碼頁，中文會變亂碼（還會吃掉變數名稱的邊界）。"""
    assert PORTPROXY.read_bytes().startswith(b"\xef\xbb\xbf")


def test_portproxy_script_variables_delimited_before_non_ascii():
    """$var 後面緊接非 ASCII 字元時一律寫成 ${var}，避免被當成變數名稱的一部分。"""
    import re

    text = PORTPROXY.read_text(encoding="utf-8-sig")
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", text)


def test_portproxy_script_prints_ip_once_and_guards_netsh_delete():
    text = PORTPROXY.read_text(encoding="utf-8-sig")
    assert text.count("WSL IP:") == 1
    delete_line = next(i for i, l in enumerate(text.splitlines()) if "portproxy delete" in l)
    before = "\n".join(text.splitlines()[max(0, delete_line - 3):delete_line])
    assert "try" in before  # 第一次執行沒有舊規則：netsh 報錯在 Stop 模式下不能中止腳本


def test_portproxy_script_validates_fallback_ipv4():
    text = PORTPROXY.read_text(encoding="utf-8-sig")
    # hostname -I 也會列出 IPv6：退回值要經過 IPv4 檢查（AddressFamily InterNetwork）
    fallback = text[text.index("(wsl hostname -I)"):]
    assert "Test-IPv4" in fallback.split("\n", 3)[1] or "Test-IPv4" in fallback[:200]
    func = text[text.index("function Test-IPv4"):text.index("(wsl hostname -I)")]
    assert "InterNetwork" in func


# ---- scripts/dev.sh（一起啟動前後端）----

import socket
import subprocess

DEV_SH = REPO / "scripts" / "dev.sh"


def _dev(*args, env_extra=None, tmp_path=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path or REPO),
           "DEV_ENV_FILE": str((tmp_path or REPO) / "nonexistent.env"), **(env_extra or {})}
    return subprocess.run(["bash", str(DEV_SH), *args], capture_output=True, text=True, env=env, timeout=30)


def test_dev_sh_syntax_and_help(tmp_path):
    assert subprocess.run(["bash", "-n", str(DEV_SH)]).returncode == 0
    r = _dev("--help", tmp_path=tmp_path)
    assert r.returncode == 0 and "--backend-only" in r.stdout


def test_dev_sh_rejects_unknown_arg(tmp_path):
    r = _dev("--nope", tmp_path=tmp_path)
    assert r.returncode == 2 and "未知參數" in r.stderr


def test_dev_sh_reports_missing_python(tmp_path):
    r = _dev("--backend-only", env_extra={"PYTHON": str(tmp_path / "no-python")}, tmp_path=tmp_path)
    assert r.returncode == 1 and "找不到 python" in r.stderr


def test_dev_sh_reports_busy_backend_port(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        r = _dev("--backend-only", env_extra={"PYTHON": sys.executable, "BACKEND_PORT": str(port)},
                 tmp_path=tmp_path)
    # sys.executable 可能沒有 uvicorn：兩種錯誤都代表沒有啟動任何服務
    assert r.returncode == 1
    assert ("已被占用" in r.stderr) or ("uvicorn" in r.stderr)


def test_dev_sh_reads_env_file_without_overriding_env(tmp_path):
    envf = tmp_path / "dev.env"
    envf.write_text('PYTHON=/from/file/python\nBACKEND_PORT=1\n', encoding="utf-8")
    r = _dev("--backend-only", env_extra={"DEV_ENV_FILE": str(envf), "PYTHON": str(tmp_path / "env-python")},
             tmp_path=tmp_path)
    # 環境變數的 PYTHON 優先於檔案
    assert "env-python" in r.stderr and "/from/file/python" not in r.stderr


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_dev_sh_comfy_url_defaults_to_settings(tmp_path, settings_file):
    r = _dev("--backend-only", "--dry-run", tmp_path=tmp_path,
             env_extra={"PYTHON": sys.executable, "BACKEND_PORT": str(_free_port()),
                        "APP_SETTINGS_FILE": str(settings_file)})
    assert r.returncode == 0, r.stderr
    assert "ComfyUI：http://comfy.test:8188" in r.stdout  # settings.yaml 的 comfy.url


def test_dev_sh_comfy_url_env_override(tmp_path, settings_file):
    r = _dev("--backend-only", "--dry-run", tmp_path=tmp_path,
             env_extra={"PYTHON": sys.executable, "BACKEND_PORT": str(_free_port()),
                        "APP_SETTINGS_FILE": str(settings_file), "APP_COMFY__URL": "http://127.0.0.1:8190/"})
    assert r.returncode == 0, r.stderr
    assert "ComfyUI：http://127.0.0.1:8190）" in r.stdout


def test_dev_sh_skips_starting_comfy_when_already_running(tmp_path):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/system_stats" else 404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        r = _dev("--comfy", "--backend-only", "--dry-run", tmp_path=tmp_path,
                 env_extra={"PYTHON": sys.executable, "BACKEND_PORT": str(_free_port()), "APP_COMFY__URL": url})
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert "略過啟動" in r.stdout and "  comfy " not in r.stdout


def test_dev_sh_rejects_comfy_port_mismatch(tmp_path):
    port = _free_port()  # 沒有服務在這個埠 → 需要啟動 ComfyUI
    r = _dev("--comfy", "--backend-only", "--dry-run", tmp_path=tmp_path,
             env_extra={"PYTHON": sys.executable, "BACKEND_PORT": str(_free_port()),
                        "APP_COMFY__URL": f"http://127.0.0.1:{port}",
                        "COMFY_START_CMD": "comfy launch -- --listen 127.0.0.1 --port 1"})
    assert r.returncode == 1 and "請讓兩者一致" in r.stderr


def test_dev_sh_allows_loopback_ip_origin(tmp_path):
    # 用 http://127.0.0.1:3000 開頁面時，Next dev 會擋 /_next/*，頁面不會 hydrate；dev.sh 要自動允許
    import shutil

    npm = shutil.which("npm")
    if npm is None or not (REPO / "frontend" / "node_modules").is_dir():
        pytest.skip("需要 npm 與 frontend/node_modules")
    r = _dev("--frontend-only", "--dry-run", tmp_path=tmp_path,
             env_extra={"PATH": f"{Path(npm).parent}:/usr/bin:/bin", "FRONTEND_PORT": str(_free_port())})
    assert r.returncode == 0, r.stderr
    line = next(l for l in r.stdout.splitlines() if "允許的開發來源" in l)
    assert "127.0.0.1" in line.split("：", 1)[1].split(",")


# ---------- scripts/invites.py 與 scripts/storage_gc.py（Phase 10 Task 7） ----------

import json as _json  # noqa: E402
import re as _re  # noqa: E402
from datetime import timedelta as _td  # noqa: E402


@pytest.fixture
def invites():
    return _load("invites")


@pytest.fixture
def storage_gc():
    return _load("storage_gc")


def _env(settings_file):
    """與 app 相同的方式開 DB 與 Storage：(settings, sessions, storage)。"""
    from app.config import load_settings
    from app.db import init_db, make_engine, session_factory
    from app.storage import make_storage

    from app.services.instance import ensure_instance

    s = load_settings(settings_file)
    eng = make_engine(s.paths.database)
    init_db(eng)
    sessions, storage = session_factory(eng), make_storage(s)
    with sessions() as sess:
        ensure_instance(sess, storage)  # 與後端啟動相同：新部署建立一致的身分標記
    return s, sessions, storage


def _same_instance(sessions, storage):
    """讓另一個 DB 帶著 storage 的身分標記（例如還原的舊備份）：只剩比例防線在擋。"""
    from app.services import instance

    with sessions() as s:
        instance.set_db_instance_id(s, instance.bucket_instance_id(storage))


def _owner(sessions, name="小明", **kw) -> tuple[int, str]:
    from app.services import auth

    with sessions() as s:
        o, token = auth.create_owner(s, name, **kw)
        return o.id, token


def _book(sessions, owner_id, **kw) -> int:
    from app.models import Book

    with sessions() as s:
        b = Book(owner_id=owner_id, title="書", paper_size="a4", print_mode="home", **kw)
        s.add(b)
        s.commit()
        return b.id


def _page(sessions, book_id, **kw) -> int:
    from app.models import Page

    with sessions() as s:
        p = Page(book_id=book_id, position=0, **kw)
        s.add(p)
        s.commit()
        return p.id


def _preview_key(gen_id: int) -> str:
    return f"cache/previews/{gen_id}_k.png"  # 舊版預覽物件（功能已移除，只有 storage_gc 會清）


def _gen(sessions, storage, book_id, page_id=None, *, kind="composite", status="succeeded", image=True,
         mask=False, discarded_days=None, request_json="{}", preview=False) -> int:
    """一筆 generation；image／mask 為 True 時寫入物件並記在列上。discarded_days：幾天前捨棄。"""
    from app.models import Generation, GenKind, GenStatus, utcnow

    with sessions() as s:
        g = Generation(book_id=book_id, page_id=page_id, kind=GenKind(kind), status=GenStatus(status),
                       user_text="", prompt="", negative_prompt="", seed=0, width=8, height=8,
                       request_json=request_json)
        if discarded_days is not None:
            g.discarded = True
            g.discarded_at = utcnow() - _td(days=discarded_days)
        s.add(g)
        s.flush()
        if image:
            g.image_path = storage.generation_image(book_id, g.id)
            storage.put(g.image_path, b"img")
        if mask:
            g.mask_path = storage.generation_mask(book_id, g.id)
            storage.put(g.mask_path, b"mask")
        if preview:
            storage.put(_preview_key(g.id), b"pv")
        s.commit()
        return g.id


def _set(sessions, model, row_id, **values):
    with sessions() as s:
        row = s.get(model, row_id)
        for k, v in values.items():
            setattr(row, k, v)
        s.add(row)
        s.commit()


def _keys(storage) -> set[str]:
    return set(storage.list("books/")) | set(storage.list("cache/"))


def _link_token(out: str, base: str) -> str:
    links = _re.findall(_re.escape(base) + r"/i/([A-Za-z0-9_-]+)", out)
    assert len(links) == 1, out  # 連結只印一次
    return links[0]


def _row(out: str, name: str) -> list[str]:
    rows = [line.split() for line in out.splitlines() if name in line.split()]
    assert len(rows) == 1, out
    return rows[0]


# --- invites.py ---


def test_invites_create_prints_link_once_and_list_hides_tokens(invites, settings_file, capsys):
    from sqlmodel import select

    from app.models import Owner, Usage
    from app.services import auth
    from app.services.usage import taipei_day

    args = ["--settings", str(settings_file)]
    assert invites.main(args + ["create", "--name", "小明", "--cap", "12",
                                "--base-url", "https://books.example/"]) == 0
    token = _link_token(capsys.readouterr().out, "https://books.example")
    assert invites.main(args + ["create", "--name", "管理員", "--admin"]) == 0
    admin_token = _link_token(capsys.readouterr().out, "http://localhost:3000")

    _, sessions, _ = _env(settings_file)
    with sessions() as s:
        o = s.exec(select(Owner).where(Owner.name == "小明")).one()
        assert o.token_hash == auth.hash_token(token) and o.daily_generation_cap == 12 and not o.is_admin
        auth.start_session(s, o)
        auth.start_session(s, o)
        s.add(Usage(owner_id=o.id, day=taipei_day(auth.utcnow()), generations=7))
        s.add(Usage(owner_id=o.id, day=taipei_day(auth.utcnow()) - _td(days=1), generations=30))
        s.commit()
        hashes = [x.token_hash for x in s.exec(select(Owner)).all()]
        oid = o.id

    assert invites.main(args + ["list"]) == 0
    out = capsys.readouterr().out
    for secret in [token, admin_token, *hashes]:
        assert secret not in out
    assert out.splitlines()[0].split() == ["id", "name", "admin", "cap", "today", "revoked", "sessions"]
    assert _row(out, "小明") == [str(oid), "小明", "no", "12", "7", "no", "2"]
    assert _row(out, "管理員")[2:] == ["yes", "unlimited", "0", "no", "0"]


def test_invites_create_base_url_from_settings(invites, settings_file, capsys, monkeypatch):
    monkeypatch.setenv("APP_AUTH__INVITE_BASE_URL", "https://cb.example/")
    assert invites.main(["--settings", str(settings_file), "create", "--name", "阿華"]) == 0
    _link_token(capsys.readouterr().out, "https://cb.example")


def test_invites_create_rejects_bad_input(invites, settings_file):
    args = ["--settings", str(settings_file), "create"]
    assert invites.main(args + ["--name", "  "]) == 1
    assert invites.main(args + ["--name", "x", "--cap", "-1"]) == 1


def test_invites_revoke_kills_sessions_and_link(invites, settings_file, capsys):
    from fastapi.testclient import TestClient
    from sqlmodel import select

    from app.config import load_settings
    from app.main import create_app
    from app.models import Owner, OwnerSession
    from tests.fake_engine import FakeEngine

    args = ["--settings", str(settings_file)]
    assert invites.main(args + ["create", "--name", "小明"]) == 0
    token = _link_token(capsys.readouterr().out, "http://localhost:3000")
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as client:
        assert client.get(f"/i/{token}", follow_redirects=False).status_code == 303
        assert client.get("/api/books").status_code == 200
        with app.state.sessions() as s:
            oid = s.exec(select(Owner).where(Owner.name == "小明")).one().id
        assert invites.main(args + ["revoke", "--id", str(oid)]) == 0
        assert client.get("/api/books").status_code == 401
        client.cookies.clear()
        r = client.get(f"/i/{token}", follow_redirects=False)
        assert r.headers["location"] == "/invalid-link"
        with app.state.sessions() as s:
            assert s.get(Owner, oid).revoked_at is not None
            assert s.exec(select(OwnerSession).where(OwnerSession.owner_id == oid)).all() == []
    assert invites.main(args + ["revoke", "--id", "999"]) == 1


def test_revoke_cancels_queued_and_refunds(invites, settings_file):
    from sqlmodel import select

    from app.models import Generation, GenStatus, PageTask, PageTaskStatus
    from app.services.usage import taipei_day
    from app.models import Usage
    from app.services import auth
    from app.services.usage import USAGE_DAY, USAGE_OWNER, USAGE_REFUNDED

    _, sessions, storage = _env(settings_file)
    d = _owner_with_data(sessions, storage, "小明")
    other = _owner_with_data(sessions, storage, "阿華")
    queued_other = other["gens"][1]
    day = taipei_day(auth.utcnow())
    _set(sessions, Generation, d["gens"][1],
         request_json=_json.dumps({USAGE_DAY: day.isoformat(), USAGE_OWNER: d["owner"]}))
    with sessions() as s:
        assert s.get(Generation, d["gens"][1]).status == GenStatus.QUEUED
        task_other = s.exec(select(PageTask).where(PageTask.page_id == other["page"])).one().id
    assert invites.main(["--settings", str(settings_file), "revoke", "--id", str(d["owner"])]) == 0
    with sessions() as s:
        g = s.get(Generation, d["gens"][1])
        assert g.status == GenStatus.CANCELLED and g.error == "邀請已撤銷"
        assert s.get(Generation, d["gens"][0]).status == GenStatus.SUCCEEDED
        task = s.exec(select(PageTask).where(PageTask.page_id == d["page"])).one()
        assert task.status == PageTaskStatus.FAILED.value and task.error == "邀請已撤銷"
        # 別人的排隊工作不受影響
        assert s.get(Generation, queued_other).status == GenStatus.QUEUED
        assert s.get(PageTask, task_other).status == PageTaskStatus.QUEUED.value
        assert _json.loads(g.request_json)[USAGE_REFUNDED] is True
        assert s.exec(select(Usage.generations).where(Usage.owner_id == d["owner"])).one() == 2


def test_revoke_leaves_running_job(invites, settings_file):
    from sqlmodel import select

    from app.models import Generation, GenStatus, PageTask, PageTaskStatus

    _, sessions, storage = _env(settings_file)
    d = _owner_with_data(sessions, storage, "小明")
    _set(sessions, Generation, d["gens"][1], status=GenStatus.RUNNING)
    with sessions() as s:
        task = s.exec(select(PageTask).where(PageTask.page_id == d["page"])).one()
        task.status = PageTaskStatus.RUNNING.value
        s.add(task)
        s.commit()
    assert invites.main(["--settings", str(settings_file), "revoke", "--id", str(d["owner"])]) == 0
    with sessions() as s:
        assert s.get(Generation, d["gens"][1]).status == GenStatus.RUNNING
        assert s.exec(select(PageTask).where(PageTask.page_id == d["page"])).one().status == PageTaskStatus.RUNNING.value


def test_invites_rotate_issues_new_link_and_drops_sessions(invites, settings_file, capsys):
    from sqlmodel import select

    from app.models import OwnerSession
    from app.services import auth

    _, sessions, _ = _env(settings_file)
    with sessions() as s:
        dev = auth.resolve_dev_owner(s, "admin")  # 遷移／本機的 admin：沒有人知道它的 token
        auth.start_session(s, dev)
        dev_id = dev.id
    oid, old = _owner(sessions)
    args = ["--settings", str(settings_file)]
    for target in (dev_id, oid):
        assert invites.main(args + ["rotate", "--id", str(target), "--base-url", "https://x.test"]) == 0
        new = _link_token(capsys.readouterr().out, "https://x.test")
        with sessions() as s:
            assert auth.owner_for_token(s, new).id == target
            assert s.exec(select(OwnerSession).where(OwnerSession.owner_id == target)).all() == []
    with sessions() as s:
        assert auth.owner_for_token(s, old) is None
    assert invites.main(args + ["revoke", "--id", str(oid)]) == 0
    assert invites.main(args + ["rotate", "--id", str(oid)]) == 1  # 已撤銷的不發新連結
    assert invites.main(args + ["rotate", "--id", "999"]) == 1


def _owner_with_data(sessions, storage, name, **kw) -> dict:
    """一個 owner：兩本書（一本有頁面、生成紀錄、修線、線稿、預覽），一個 session，兩天的用量。"""
    from app.models import LineartEdit, Owner, Page, PageHistory, PageTask, StrokeLayer, Usage
    from app.services import auth
    from app.services.usage import taipei_day

    oid, _ = _owner(sessions, name, **kw)
    b1 = _book(sessions, oid)
    b2 = _book(sessions, oid)
    p = _page(sessions, b1)
    g = _gen(sessions, storage, b1, p, kind="pose", mask=True)
    q = _gen(sessions, storage, b1, p, status="queued", image=False)
    lin = storage.lineart(b1, p, "abc")
    storage.put(lin, b"lin")
    _set(sessions, Page, p, lineart_path=lin, lineart_key="abc",
         selected_generation_id=g)
    with sessions() as s:
        s.add(LineartEdit(generation_id=g, page_id=p, strokes_json="[]"))
        s.add(LineartEdit(generation_id=g, page_id=p, layer=StrokeLayer.AUTO, strokes_json="[]"))
        s.add(PageTask(page_id=p))
        s.add(PageHistory(page_id=p, seq=1, snapshot_json="{}"))
        owner = s.get(Owner, oid)
        auth.start_session(s, owner)
        s.add(Usage(owner_id=oid, day=taipei_day(auth.utcnow()), generations=3))
        s.commit()
    return {"owner": oid, "books": [b1, b2], "page": p, "gens": [g, q]}


def _counts(sessions, d) -> dict:
    from sqlmodel import col, select

    from app.models import Book, Generation, LineartEdit, Owner, OwnerSession, Page, PageHistory, PageTask, Usage

    with sessions() as s:
        return {
            "owner": s.get(Owner, d["owner"]) is not None,
            "books": len(s.exec(select(Book).where(Book.owner_id == d["owner"])).all()),
            "pages": len(s.exec(select(Page).where(col(Page.book_id).in_(d["books"]))).all()),
            "gens": len(s.exec(select(Generation).where(col(Generation.book_id).in_(d["books"]))).all()),
            "edits": len(s.exec(select(LineartEdit).where(LineartEdit.page_id == d["page"])).all()),
            "tasks": len(s.exec(select(PageTask).where(PageTask.page_id == d["page"])).all()),
            "history": len(s.exec(select(PageHistory).where(PageHistory.page_id == d["page"])).all()),
            "sessions": len(s.exec(select(OwnerSession).where(OwnerSession.owner_id == d["owner"])).all()),
            "usage": len(s.exec(select(Usage).where(Usage.owner_id == d["owner"])).all()),
        }


def _owned_keys(storage, d) -> set[str]:
    return {k for b in d["books"] for k in storage.list(f"books/{b}/")}


def test_delete_data_removes_rows_and_objects_only_for_that_owner(invites, settings_file, capsys):
    _, sessions, storage = _env(settings_file)
    _owner(sessions, "管理員", is_admin=True)
    a = _owner_with_data(sessions, storage, "小明")
    b = _owner_with_data(sessions, storage, "阿華")
    before_b = _counts(sessions, b)
    keys_b = _owned_keys(storage, b)
    assert len(_owned_keys(storage, a)) == 3 and len(keys_b) == 3

    assert invites.main(["--settings", str(settings_file), "delete-data", "--id", str(a["owner"]), "--yes"]) == 0
    assert before_b["edits"] == 2 and before_b["tasks"] == 1 and before_b["history"] == 1
    assert _counts(sessions, a) == {"owner": False, "books": 0, "pages": 0, "gens": 0, "edits": 0,
                                    "tasks": 0, "history": 0, "sessions": 0, "usage": 0}
    assert _owned_keys(storage, a) == set()
    assert _counts(sessions, b) == before_b and _owned_keys(storage, b) == keys_b
    assert "小明" in capsys.readouterr().out


def test_delete_data_asks_for_the_name(invites, settings_file, monkeypatch):
    _, sessions, storage = _env(settings_file)
    _owner(sessions, "管理員", is_admin=True)
    a = _owner_with_data(sessions, storage, "小明")
    before = _counts(sessions, a)
    args = ["--settings", str(settings_file), "delete-data", "--id", str(a["owner"])]
    monkeypatch.setattr("builtins.input", lambda prompt="": "小")
    assert invites.main(args) == 1
    assert _counts(sessions, a) == before
    monkeypatch.setattr("builtins.input", lambda prompt="": "小明")
    assert invites.main(args) == 0
    assert _counts(sessions, a)["owner"] is False


def test_delete_data_refuses_last_admin(invites, settings_file):
    from app.models import Owner, utcnow

    _, sessions, storage = _env(settings_file)
    admin = _owner_with_data(sessions, storage, "管理員", is_admin=True)
    _owner(sessions, "小明")
    old_admin, _ = _owner(sessions, "舊管理員", is_admin=True)
    _set(sessions, Owner, old_admin, revoked_at=utcnow())  # 已撤銷的 admin 不算
    args = ["--settings", str(settings_file), "delete-data", "--id", str(admin["owner"]), "--yes"]
    assert invites.main(args) == 1
    assert _counts(sessions, admin)["books"] == 2
    _owner(sessions, "新管理員", is_admin=True)
    assert invites.main(args) == 0
    assert _counts(sessions, admin)["owner"] is False


def test_delete_data_force_admin(invites, settings_file):
    _, sessions, storage = _env(settings_file)
    admin = _owner_with_data(sessions, storage, "管理員", is_admin=True)
    args = ["--settings", str(settings_file), "delete-data", "--id", str(admin["owner"]), "--yes"]
    assert invites.main(args + ["--force-admin"]) == 0
    assert _counts(sessions, admin)["owner"] is False


def test_delete_data_refuses_while_generating(invites, settings_file):
    from app.models import Generation, GenStatus

    _, sessions, storage = _env(settings_file)
    _owner(sessions, "管理員", is_admin=True)
    a = _owner_with_data(sessions, storage, "小明")
    _gen(sessions, storage, a["books"][1], status="running", image=False)
    before = _counts(sessions, a)
    keys = _owned_keys(storage, a)
    args = ["--settings", str(settings_file), "delete-data", "--id", str(a["owner"]), "--yes"]
    assert invites.main(args) == 1
    assert _counts(sessions, a) == before and _owned_keys(storage, a) == keys
    with sessions() as s:
        assert s.get(Generation, a["gens"][1]).status == GenStatus.QUEUED  # 取消已 rollback
    assert invites.main(["--settings", str(settings_file), "delete-data", "--id", "999", "--yes"]) == 1


def test_delete_data_refuses_while_page_pipeline_running(invites, settings_file):
    """頁面流程（page_task）執行中也拒絕：它正在寫這個 owner 的合成圖與線稿。"""
    from sqlmodel import select

    from app.models import Generation, GenStatus, PageTask

    _, sessions, storage = _env(settings_file)
    _owner(sessions, "管理員", is_admin=True)
    a = _owner_with_data(sessions, storage, "小明")
    with sessions() as s:
        t = s.exec(select(PageTask).where(PageTask.page_id == a["page"])).one()
        t.status = "running"
        s.add(t)
        s.commit()
        tid = t.id
    before = _counts(sessions, a)
    keys = _owned_keys(storage, a)
    args = ["--settings", str(settings_file), "delete-data", "--id", str(a["owner"]), "--yes"]
    assert invites.main(args) == 1
    assert _counts(sessions, a) == before and _owned_keys(storage, a) == keys
    with sessions() as s:
        assert s.get(Generation, a["gens"][1]).status == GenStatus.QUEUED  # 取消已 rollback
        t = s.get(PageTask, tid)
        t.status = "succeeded"
        s.add(t)
        s.commit()
    assert invites.main(args) == 0


# --- storage_gc.py ---


def _gc_world(settings_file):
    """一本書、一頁，各種 generation 與物件。回傳 (sessions, storage, ids, expected)。"""
    from app.models import Book, Generation, Page

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "管理員", is_admin=True)
    b = _book(sessions, oid)
    p = _page(sessions, b)
    ids = {}
    ids["old"] = _gen(sessions, storage, b, p, kind="pose", mask=True, discarded_days=31, preview=True)
    ids["recent"] = _gen(sessions, storage, b, p, discarded_days=5)
    ids["live"] = _gen(sessions, storage, b, p, preview=True)
    for name in ("sel", "bg", "pose", "char", "ref"):
        ids[name] = _gen(sessions, storage, b, p, mask=(name == "pose"), discarded_days=40)
    ref_key = storage.generation_image(b, ids["ref"])
    ids["queued"] = _gen(sessions, storage, b, p, kind="pose", status="queued", image=False,
                         request_json=_json.dumps({"character_image": ref_key}))
    ids["running"] = _gen(sessions, storage, b, p, status="running", image=False)
    storage.put(storage.generation_image(b, ids["running"]), b"partial")  # worker 寫了、還沒記到列上
    ids["gone"] = _gen(sessions, storage, b, p, mask=True, preview=True)
    with sessions() as s:
        s.delete(s.get(Generation, ids["gone"]))
        s.commit()
    inflight = ids["gone"] + 10  # 還沒 commit 的列（id 大於 sqlite_sequence）：物件先寫、列後 commit
    storage.put(storage.generation_image(b, inflight), b"new")
    storage.put(_preview_key(inflight), b"pv")
    lin_cur, lin_old = storage.lineart(b, p, "cur"), storage.lineart(b, p, "old")
    storage.put(lin_cur, b"l")
    storage.put(lin_old, b"l")
    sketch = f"books/{b}/pages/{p}/sketch.png"  # 舊版欄位（已移除）留下的檔案
    storage.put(sketch, b"s")
    # DB 對不上的物件不刪（可能是別的 DB 的）：書 id 大於 DB 最大的書、生成紀錄屬於另一本書
    other_book = _book(sessions, oid)
    beyond = storage.generation_image(other_book + 50, ids["gone"])
    storage.put(beyond, b"x")
    wrong_book = storage.generation_image(other_book, ids["live"])
    storage.put(wrong_book, b"x")
    _set(sessions, Page, p, selected_generation_id=ids["sel"], background_generation_id=ids["bg"],
         pose_generation_id=ids["pose"], lineart_path=lin_cur, lineart_key="cur")
    _set(sessions, Book, b, character_generation_id=ids["char"])

    expired = {storage.generation_image(b, ids["old"]), storage.generation_mask(b, ids["old"])}
    orphans = {storage.generation_image(b, ids["gone"]), storage.generation_mask(b, ids["gone"]),
               lin_old, sketch}
    # 一次性清理：cache/previews/ 底下全部刪（不管生成紀錄還在不在、是否進行中）
    orphan_previews = {_preview_key(ids[n]) for n in ("old", "live", "gone")} | {_preview_key(inflight)}
    return sessions, storage, ids, {"expired": expired, "orphans": orphans, "orphan_previews": orphan_previews,
                                    "mismatched": {beyond, wrong_book}}


def _paths(sessions) -> dict:
    from sqlmodel import select

    from app.models import Generation

    with sessions() as s:
        return {g.id: (g.image_path, g.mask_path) for g in s.exec(select(Generation)).all()}


def test_gc_dry_run_deletes_nothing(storage_gc, settings_file, capsys):
    sessions, storage, ids, exp = _gc_world(settings_file)
    keys, paths = _keys(storage), _paths(sessions)
    assert storage_gc.main(["--settings", str(settings_file), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert _keys(storage) == keys and _paths(sessions) == paths
    assert "dry-run" in out.lower()
    rep = storage_gc.run_gc(sessions, storage, days=30, dry_run=True)
    assert (rep.expired_generations, rep.expired_objects) == (1, 2)
    assert (rep.orphan_objects, rep.orphan_previews) == (4, 4)
    assert (rep.skipped_in_flight, rep.skipped_mismatched) == (2, 2) and not rep.aborted
    assert _keys(storage) == keys and _paths(sessions) == paths


def test_gc_deletes_only_expired_discarded_and_orphans(storage_gc, settings_file, capsys):
    sessions, storage, ids, exp = _gc_world(settings_file)
    keys, paths = _keys(storage), _paths(sessions)
    assert storage_gc.main(["--settings", str(settings_file)]) == 0
    out = capsys.readouterr().out
    gone = exp["expired"] | exp["orphans"] | exp["orphan_previews"]
    assert gone <= keys
    assert _keys(storage) == keys - gone
    after = _paths(sessions)
    assert after[ids["old"]] == (None, None)  # 物件刪了，key 清掉
    for name in ("recent", "live", "sel", "bg", "pose", "char", "ref"):
        assert after[ids[name]] == paths[ids[name]], name  # 選用中的、角色、排隊任務要用的，捨棄再久也不刪
    assert "1 筆，已刪除物件 2 個" in out and "books/ 4 個、cache/previews/ 4 個" in out
    assert "對不上）：2 個" in out
    # 再跑一次：沒有東西可刪
    rep = storage_gc.run_gc(sessions, storage, days=30, dry_run=False)
    assert (rep.expired_generations, rep.expired_objects, rep.orphan_objects, rep.orphan_previews) == (0, 0, 0, 0)


def _gc_ref_world(settings_file):
    from app.models import Page

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "管理員", is_admin=True)
    b = _book(sessions, oid)
    p = _page(sessions, b)
    old = _gen(sessions, storage, b, p, kind="pose", discarded_days=40)
    return sessions, storage, b, p, old, Page


def _gc_kept(storage_gc, sessions, storage, old) -> bool:
    storage_gc.run_gc(sessions, storage, days=30, dry_run=False)
    return _paths(sessions)[old][0] is not None


def _gc_unreferenced_deleted(storage_gc, settings_file) -> None:
    sessions, storage, b, p, old, _ = _gc_ref_world(settings_file)
    assert not _gc_kept(storage_gc, sessions, storage, old)


@pytest.mark.parametrize("field", ["auto_background_generation_id", "auto_pose_generation_id"])
def test_gc_keeps_auto_selected_generations(storage_gc, settings_file, field):
    sessions, storage, b, p, old, Page = _gc_ref_world(settings_file)
    _set(sessions, Page, p, **{field: old})
    assert _gc_kept(storage_gc, sessions, storage, old)
    assert storage.exists(storage.generation_image(b, old))


@pytest.mark.parametrize("field", ["background_generation_id", "pose_generation_id"])
def test_gc_keeps_generations_referenced_by_history_snapshots(storage_gc, settings_file, field):
    from app.models import PageHistory

    sessions, storage, b, p, old, _ = _gc_ref_world(settings_file)
    with sessions() as s:
        s.add(PageHistory(page_id=p, seq=1, snapshot_json=_json.dumps({field: old, "user_strokes": []})))
        s.commit()
    assert _gc_kept(storage_gc, sessions, storage, old)


@pytest.mark.parametrize("status,kept", [("queued", True), ("running", True), ("succeeded", False), ("failed", False)])
def test_gc_keeps_generations_of_pages_with_active_tasks(storage_gc, settings_file, status, kept):
    from app.models import PageTask

    sessions, storage, b, p, old, _ = _gc_ref_world(settings_file)
    with sessions() as s:
        s.add(PageTask(page_id=p, status=status))
        s.commit()
    assert _gc_kept(storage_gc, sessions, storage, old) is kept


def test_gc_deletes_unreferenced_old_discarded_generation(storage_gc, settings_file):
    _gc_unreferenced_deleted(storage_gc, settings_file)


def test_gc_snapshot_with_garbage_json_is_ignored(storage_gc, settings_file):
    from app.models import PageHistory

    sessions, storage, b, p, old, _ = _gc_ref_world(settings_file)
    with sessions() as s:
        s.add(PageHistory(page_id=p, seq=1, snapshot_json="not json"))
        s.add(PageHistory(page_id=p, seq=2, snapshot_json="[1]"))
        s.commit()
    assert not _gc_kept(storage_gc, sessions, storage, old)


def test_gc_days_option(storage_gc, settings_file):
    sessions, storage, ids, _ = _gc_world(settings_file)
    assert storage_gc.main(["--settings", str(settings_file), "--days", "3"]) == 0
    after = _paths(sessions)
    assert after[ids["recent"]] == (None, None) and after[ids["old"]] == (None, None)
    assert after[ids["live"]][0] is not None and after[ids["sel"]][0] is not None
    assert storage_gc.main(["--settings", str(settings_file), "--days", "-1"]) == 2


def test_gc_never_clears_a_generation_that_became_selected(storage_gc, settings_file, monkeypatch):
    # 讀完 DB 之後才被選用（另一個行程）：清 key 的 UPDATE 重新檢查，不清也不刪
    from sqlmodel import select

    from app.models import Page

    sessions, storage, ids, _ = _gc_world(settings_file)
    real = storage_gc._load_state

    def racing(sess, now, days):
        st = real(sess, now, days)
        with sessions() as s:
            page_id = s.exec(select(Page)).first().id
        _set(sessions, Page, page_id, selected_generation_id=ids["old"])
        return st

    monkeypatch.setattr(storage_gc, "_load_state", racing)
    before = _paths(sessions)[ids["old"]]
    rep = storage_gc.run_gc(sessions, storage, days=30, dry_run=False)
    assert rep.expired_generations == 0
    assert _paths(sessions)[ids["old"]] == before
    assert all(storage.exists(k) for k in before)


@pytest.fixture
def no_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise RuntimeError(f"測試不可連網：{args!r}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def test_gc_on_r2_deletes_through_cached_storage(storage_gc, settings_file, tmp_path, monkeypatch, no_network):
    import boto3
    import moto

    from app.storage import CachedStorage

    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    cache = tmp_path / "r2cache"
    for k, v in {"BACKEND": "r2", "R2__ENDPOINT": "https://s3.amazonaws.com", "R2__BUCKET": "gc-bucket",
                 "R2__ACCESS_KEY": "AK", "R2__SECRET_KEY": "SK", "CACHE_DIR": str(cache)}.items():
        monkeypatch.setenv(f"APP_STORAGE__{k}", v)
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="gc-bucket")
        sessions, storage, ids, exp = _gc_world(settings_file)
        assert isinstance(storage, CachedStorage)
        old = storage.generation_image(1, ids["old"])
        assert storage.get(old) == b"img" and (cache / "gc-bucket" / old).is_file()  # 快取裡也有一份（依 bucket 分資料夾）
        assert storage_gc.main(["--settings", str(settings_file)]) == 0
        assert not storage.inner.exists(old)
        assert not (cache / "gc-bucket" / old).exists()
        assert not any(storage.inner.exists(k) for k in exp["orphans"] | exp["orphan_previews"])
        assert storage.inner.exists(storage.generation_image(1, ids["sel"]))


@pytest.mark.parametrize("other_book_id", [101, 1], ids=["disjoint", "overlapping"])
def test_gc_mismatched_db_deletes_nothing_live(storage_gc, settings_file, tmp_path, other_book_id):
    """另一個不相干的 DB（例如開發用的）接到同一個 storage：什麼都不刪。
    disjoint：對方的列屬於別本書 → 對不上，略過；overlapping：對方的列書號相同、沒有圖 → 孤兒太多，整個中止。"""
    from app.db import init_db, make_engine, session_factory
    from app.models import Page

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    p = _page(sessions, b)
    live = [_gen(sessions, storage, b, p) for _ in range(30)]
    lin = storage.lineart(b, p, "cur")
    storage.put(lin, b"l")
    _set(sessions, Page, p, lineart_path=lin, selected_generation_id=live[0])

    eng = make_engine(tmp_path / "other.db")
    init_db(eng)
    other = session_factory(eng)
    _same_instance(other, storage)
    o2, _ = _owner(other, "x", is_admin=True)
    b2 = _book(other, o2)
    assert b2 == b
    if other_book_id == b:
        _page(other, b2)
    for _ in range(40):
        _gen(other, storage, other_book_id, None, image=False)  # 列存在、沒有圖
    before = _keys(storage)
    rep = storage_gc.run_gc(other, storage, days=30)
    assert _keys(storage) == before
    assert rep.aborted and rep.orphan_objects == rep.orphan_previews == 0
    assert "--allow-mass-delete" in storage_gc.format_report(rep)
    if other_book_id != b:
        assert rep.skipped_mismatched > 20  # 對不上的物件太多：DB 與 storage 不是一組


def _mass_orphans(settings_file, n=25):
    """n 筆已刪除的生成紀錄留下的圖（真的孤兒），加上 3 筆還在的。"""
    from app.models import Generation

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    gone = [_gen(sessions, storage, b) for _ in range(n)]
    live = [_gen(sessions, storage, b) for _ in range(3)]
    with sessions() as s:
        for gid in gone:
            s.delete(s.get(Generation, gid))
        s.commit()
    return sessions, storage, gone, live


def test_gc_aborts_mass_delete_unless_allowed(storage_gc, settings_file, capsys):
    sessions, storage, gone, live = _mass_orphans(settings_file)
    before = _keys(storage)
    args = ["--settings", str(settings_file)]
    assert storage_gc.main(args + ["--dry-run"]) == 3
    assert storage_gc.main(args) == 3
    out = capsys.readouterr()
    assert "--allow-mass-delete" in out.out + out.err
    assert _keys(storage) == before
    assert storage_gc.main(args + ["--allow-mass-delete"]) == 0
    assert _keys(storage) == {storage.generation_image(1, g) for g in live} | {"books/_instance.json"}


def test_gc_small_orphan_count_is_not_mass(storage_gc, settings_file):
    # 比例超過 20% 但不超過 20 個：照常刪
    sessions, storage, gone, live = _mass_orphans(settings_file, n=20)
    rep = storage_gc.run_gc(sessions, storage, days=30)
    assert not rep.aborted and rep.orphan_objects == 20


def test_gc_refuses_db_not_at_head(storage_gc, settings_file, tmp_path, monkeypatch):
    # GC 不跑 alembic 升級：空白或舊版的 DB 一律拒絕，請先啟動後端
    from alembic import command

    from app.db import alembic_config, make_engine

    db = tmp_path / "empty.db"
    db.touch()
    monkeypatch.setenv("APP_PATHS__DATABASE", str(db))
    assert storage_gc.main(["--settings", str(settings_file), "--dry-run"]) == 2
    assert db.stat().st_size == 0  # 沒有建任何資料表

    monkeypatch.delenv("APP_PATHS__DATABASE")
    _env(settings_file)  # 升到最新
    from app.config import load_settings

    eng = make_engine(load_settings(settings_file).paths.database)
    cfg = alembic_config(eng)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0005")
    assert storage_gc.main(["--settings", str(settings_file), "--dry-run"]) == 2


def test_delete_data_rechecks_last_admin_inside_transaction(invites, settings_file, monkeypatch):
    # 確認期間另一個 admin 被撤銷：刪除的交易裡要再檢查一次
    from app.models import Owner, utcnow

    _, sessions, storage = _env(settings_file)
    admin = _owner_with_data(sessions, storage, "管理員", is_admin=True)
    other, _ = _owner(sessions, "另一個管理員", is_admin=True)

    def answer(prompt=""):
        _set(sessions, Owner, other, revoked_at=utcnow())
        return "管理員"

    monkeypatch.setattr("builtins.input", answer)
    assert invites.main(["--settings", str(settings_file), "delete-data", "--id", str(admin["owner"])]) == 1
    assert _counts(sessions, admin)["owner"] is True and _counts(sessions, admin)["books"] == 2


def _bulk_gens(sessions, book_id, n, **kw):
    """一次新增 n 筆沒有圖的成功生成紀錄（不寫物件）。"""
    from app.models import Generation, GenKind, GenStatus

    with sessions() as s:
        for _ in range(n):
            s.add(Generation(book_id=book_id, kind=GenKind.COMPOSITE, status=GenStatus.SUCCEEDED, user_text="",
                             prompt="", negative_prompt="", seed=0, width=8, height=8, request_json="{}", **kw))
        s.commit()


def test_gc_dilution_by_mismatched_keys_still_aborts(storage_gc, settings_file, tmp_path, monkeypatch, capsys):
    """1000 個物件：另一個 DB 只有 1 本書、100 筆沒有圖的列 → 900 個對不上（書號超出）、100 個像孤兒。
    以前用全部列出的物件當分母只有 10%，會刪掉 100 個活的物件；現在整個中止、結束碼 3。"""
    from app.db import init_db, make_engine, session_factory

    _, _, storage = _env(settings_file)
    for gid in range(1, 1001):
        storage.put(storage.generation_image(1 + (gid - 1) // 100, gid), b"live")
    other_db = tmp_path / "other.db"
    eng = make_engine(other_db)
    init_db(eng)
    other = session_factory(eng)
    _same_instance(other, storage)
    oid, _ = _owner(other, "x", is_admin=True)
    _bulk_gens(other, _book(other, oid), 100)
    before = _keys(storage)
    monkeypatch.setenv("APP_PATHS__DATABASE", str(other_db))
    assert storage_gc.main(["--settings", str(settings_file)]) == 3
    assert _keys(storage) == before
    assert "--allow-mass-delete" in capsys.readouterr().err
    rep = storage_gc.run_gc(other, storage, days=30, dry_run=True)
    assert rep.aborted and (rep.skipped_mismatched, rep.mass_candidates) == (900, 100)


def test_gc_ratio_ignores_skipped_keys(storage_gc, settings_file):
    """分母是孤兒＋被參照的物件，不含略過的：25 個孤兒、50 個活的、200 個正在寫入的 → 33%，中止。"""
    from app.models import Generation

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    gone = [_gen(sessions, storage, b) for _ in range(25)]
    [_gen(sessions, storage, b) for _ in range(50)]
    with sessions() as s:
        for gid in gone:
            s.delete(s.get(Generation, gid))
        s.commit()
    for gid in range(1000, 1200):  # id 大於 sqlite_sequence：正在寫入
        storage.put(storage.generation_image(b, gid), b"new")
    before = _keys(storage)
    rep = storage_gc.run_gc(sessions, storage, days=30)
    assert rep.aborted and rep.skipped_in_flight == 200 and rep.skipped_mismatched == 0
    assert _keys(storage) == before
    rep = storage_gc.run_gc(sessions, storage, days=30, allow_mass_delete=True)
    assert not rep.aborted and rep.orphan_objects == 25


def test_gc_mismatched_guard_overridden_by_allow(storage_gc, settings_file):
    # 只有「對不上」的物件很多（沒有孤兒）也中止；--allow-mass-delete 時照常跑，對不上的仍然不刪
    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    live = _gen(sessions, storage, b)
    for i in range(30):
        storage.put(storage.generation_image(b + 1 + i, 5000 + i), b"x")
    before = _keys(storage)
    rep = storage_gc.run_gc(sessions, storage, days=30)
    assert rep.aborted and rep.skipped_mismatched == 30 and rep.mass_candidates == 0
    rep = storage_gc.run_gc(sessions, storage, days=30, allow_mass_delete=True)
    assert not rep.aborted and _keys(storage) == before
    assert storage.exists(storage.generation_image(b, live))


def test_gc_deletes_every_old_preview_object(storage_gc, settings_file):
    """一次性清理：cache/previews/ 底下全部刪（預覽功能已移除），不看生成紀錄；dry-run 不刪；books/ 的物件不動。"""
    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    live = _gen(sessions, storage, b, preview=True)
    previews = [_preview_key(live), "cache/previews/999_k.png", "cache/previews/odd-name.png"]
    for k in previews[1:]:
        storage.put(k, b"pv")
    before = _keys(storage)
    rep = storage_gc.run_gc(sessions, storage, days=30, dry_run=True)
    assert rep.orphan_previews == 3 and _keys(storage) == before
    rep = storage_gc.run_gc(sessions, storage, days=30)
    assert rep.orphan_previews == 3 and not rep.aborted
    assert not any(storage.exists(k) for k in previews)
    assert storage.exists(storage.generation_image(b, live))


def test_gc_small_overlap_wrong_db_deletes_nothing(storage_gc, settings_file, tmp_path):
    """對不上的 DB（10 筆同 id、沒有圖、屬於另一本書的列）低於中止門檻：活的圖一個都不刪。"""
    from app.db import init_db, make_engine, session_factory
    from app.models import Page

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    p = _page(sessions, b)
    live = [_gen(sessions, storage, b, p) for _ in range(20)]
    _set(sessions, Page, p, selected_generation_id=live[0])
    eng = make_engine(tmp_path / "other.db")
    init_db(eng)
    other = session_factory(eng)
    _same_instance(other, storage)
    o2, _ = _owner(other, "x", is_admin=True)
    _bulk_gens(other, _book(other, o2) + 100, 10)
    before = _keys(storage)
    rep = storage_gc.run_gc(other, storage, days=30)
    assert not rep.aborted and _keys(storage) == before


# --- 身分標記（storage_instance_id） ---


def _other_db(tmp_path, storage=None, *, same_id=False, name="other.db"):
    """另一個（錯的）DB；same_id=False 時它有自己的身分標記（像另一個部署）。"""
    from app.db import init_db, make_engine, session_factory
    from app.services import instance

    path = tmp_path / name
    eng = make_engine(path)
    init_db(eng)
    other = session_factory(eng)
    with other() as s:
        instance.set_db_instance_id(s, instance.bucket_instance_id(storage) if same_id else "a" * 32)
    return path, other


def test_gc_refuses_wrong_db_with_full_id_overlap(storage_gc, settings_file, tmp_path, monkeypatch, capsys):
    """≤ 20 個物件、id 完全重疊的錯 DB：比例防線擋不到，身分標記擋下來（結束碼 4，什麼都不刪）。"""
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    [_gen(sessions, storage, b) for _ in range(10)]
    path, other = _other_db(tmp_path, storage)
    o2, _ = _owner(other, "x", is_admin=True)
    _bulk_gens(other, _book(other, o2), 10)  # 同書號、同 id、沒有圖：每個物件看起來都是孤兒
    before = _keys(storage)
    monkeypatch.setenv("APP_PATHS__DATABASE", str(path))
    for extra in ([], ["--allow-mass-delete"], ["--dry-run"]):
        assert storage_gc.main(["--settings", str(settings_file), *extra]) == 4
    assert _keys(storage) == before
    err = capsys.readouterr().err
    assert "a" * 32 in err and instance.bucket_instance_id(storage) in err


def test_gc_refuses_mixed_wrong_db(storage_gc, settings_file, tmp_path, monkeypatch):
    # 100 個被參照＋20 個沒被參照（低於比例門檻）：DB 沒有身分標記 → 結束碼 4
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    [_gen(sessions, storage, b) for _ in range(120)]
    path, other = _other_db(tmp_path, storage)
    with other() as s:
        instance.delete_db_instance_id(s)
    o2, _ = _owner(other, "x", is_admin=True)
    ob = _book(other, o2)
    with other() as s:  # 前 100 筆有圖（被參照）、後 20 筆沒有
        from app.models import Generation, GenKind, GenStatus

        for gid in range(1, 121):
            s.add(Generation(id=gid, book_id=ob, kind=GenKind.COMPOSITE, status=GenStatus.SUCCEEDED, user_text="",
                             prompt="", negative_prompt="", seed=0, width=8, height=8, request_json="{}",
                             image_path=storage.generation_image(ob, gid) if gid <= 100 else None))
        s.commit()
    before = _keys(storage)
    monkeypatch.setenv("APP_PATHS__DATABASE", str(path))
    assert storage_gc.main(["--settings", str(settings_file)]) == 4
    assert _keys(storage) == before
    # 同一個情境若身分標記一致，比例防線不會擋（20 個 ≤ 門檻）：這正是身分標記要補的洞
    _same_instance(other, storage)
    rep = storage_gc.run_gc(other, storage, days=30, dry_run=True)
    assert not rep.aborted and rep.orphan_objects == 20


def test_gc_refuses_when_bucket_marker_missing_and_init_instance(storage_gc, settings_file, capsys):
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    with sessions() as s:
        db_id = instance.db_instance_id(s)
    storage.delete(instance.MARKER_KEY)
    args = ["--settings", str(settings_file)]
    assert storage_gc.main(args) == 4
    assert "--init-instance" in capsys.readouterr().err
    assert storage_gc.main(args + ["--init-instance", "--yes"]) == 0
    assert instance.bucket_instance_id(storage) == db_id
    assert storage_gc.main(args + ["--init-instance"]) == 0  # 已一致：不變、不詢問
    assert storage_gc.main(args + ["--dry-run"]) == 0


def test_init_instance_never_overwrites_a_differing_marker(storage_gc, settings_file):
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    instance.write_bucket_instance_id(storage, "b" * 32)
    args = ["--settings", str(settings_file)]
    assert storage_gc.main(args + ["--init-instance"]) == 4
    assert storage_gc.main(args + ["--allow-mass-delete"]) == 4
    assert instance.bucket_instance_id(storage) == "b" * 32


def test_init_instance_both_missing_writes_nothing_without_confirmation(storage_gc, settings_file, monkeypatch):
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    storage.delete(instance.MARKER_KEY)
    with sessions() as s:
        instance.delete_db_instance_id(s)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    assert storage_gc.main(["--settings", str(settings_file), "--init-instance"]) == 1
    assert instance.bucket_instance_id(storage) is None
    with sessions() as s:
        assert instance.db_instance_id(s) is None


def test_run_gc_refuses_mismatch_directly(storage_gc, settings_file):
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    instance.write_bucket_instance_id(storage, "c" * 32)
    with pytest.raises(storage_gc.InstanceMismatch):
        storage_gc.run_gc(sessions, storage, days=30)


def test_marker_never_deleted_by_gc_or_delete_data(storage_gc, invites, settings_file):
    from app.services import instance

    sessions, storage, gone, live = _mass_orphans(settings_file)
    marker = instance.bucket_instance_id(storage)
    rep = storage_gc.run_gc(sessions, storage, days=30, allow_mass_delete=True)
    assert rep.orphan_objects == 25 and instance.MARKER_KEY not in [k for _, k in rep.actions]
    assert instance.bucket_instance_id(storage) == marker
    _owner(sessions, "管理員", is_admin=True)
    a = _owner_with_data(sessions, storage, "小明")
    assert invites.main(["--settings", str(settings_file), "delete-data", "--id", str(a["owner"]), "--yes"]) == 0
    assert instance.bucket_instance_id(storage) == marker


# --- 修正第 4 輪：快取裡的舊標記、delete-data 的標記檢查、空 DB 不認領、--init-instance 的復原路徑 ---


@pytest.fixture
def r2_two_buckets(monkeypatch, tmp_path):
    import boto3
    import moto

    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    for k, v in {"BACKEND": "r2", "R2__ENDPOINT": "https://s3.amazonaws.com", "R2__BUCKET": "bucket-a",
                 "R2__ACCESS_KEY": "AK", "R2__SECRET_KEY": "SK", "CACHE_DIR": str(tmp_path / "r2cache")}.items():
        monkeypatch.setenv(f"APP_STORAGE__{k}", v)
    with moto.mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="bucket-a")
        c.create_bucket(Bucket="bucket-b")
        yield tmp_path / "r2cache"


@pytest.mark.parametrize("b_marker", [None, "b" * 32])
def test_gc_bucket_switch_with_shared_cache_dir_exits_4(storage_gc, settings_file, monkeypatch, no_network,
                                                        r2_two_buckets, capsys, b_marker):
    """DB A＋bucket A 的標記讀過（進了快取），之後設定換成 bucket B、快取資料夾不變：GC 必須拒絕。"""
    from app.config import load_settings
    from app.services import instance
    from app.storage import CachedStorage, make_storage

    _, sessions, storage_a = _env(settings_file)
    assert instance.bucket_instance_id(storage_a)
    storage_a.get(instance.MARKER_KEY)  # 放進快取
    oid, _ = _owner(sessions, "a", is_admin=True)
    b = _book(sessions, oid)
    _bulk_gens(sessions, b, 15)
    monkeypatch.setenv("APP_STORAGE__R2__BUCKET", "bucket-b")
    storage_b = make_storage(load_settings(settings_file))
    for gid in range(1, 11):
        storage_b.inner.put(storage_b.generation_image(b, gid), b"live-b")
    if b_marker:
        storage_b.inner.put(instance.MARKER_KEY, _json.dumps({"storage_instance_id": b_marker}).encode())
    # 即使兩個 bucket 共用同一個（未分 bucket 的）快取資料夾，標記也不經過快取
    shared = CachedStorage(storage_b.inner, storage_a.cache_dir, 10_000_000)
    assert instance.bucket_instance_id(shared) == b_marker
    before = set(storage_b.inner.list("books/"))
    assert storage_gc.main(["--settings", str(settings_file)]) == 4
    assert storage_gc.main(["--settings", str(settings_file), "--allow-mass-delete"]) == 4
    assert set(storage_b.inner.list("books/")) == before
    assert "什麼都沒刪" in capsys.readouterr().err


def _wrong_db_victim(settings_file, tmp_path, *, db_id="a" * 32):
    """live DB（books 1、2 有物件）＋錯的 DB（同書號、同 owner id 的 victim）。"""
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    _owner(sessions, "admin", is_admin=True)
    live = _owner_with_data(sessions, storage, "live")
    path, other = _other_db(tmp_path, storage)
    with other() as s:
        if db_id is None:
            instance.delete_db_instance_id(s)
        else:
            instance.set_db_instance_id(s, db_id)
    _owner(other, "admin2", is_admin=True)
    vid, _ = _owner(other, "victim")
    assert [_book(other, vid), _book(other, vid)] == live["books"]
    return sessions, storage, other, path, vid


@pytest.mark.parametrize("db_id", ["a" * 32, None], ids=["differ", "db_missing"])
def test_delete_data_refuses_wrong_db(invites, settings_file, tmp_path, monkeypatch, capsys, db_id):
    from sqlmodel import select

    from app.models import Book, Owner
    from app.services import instance

    sessions, storage, other, path, vid = _wrong_db_victim(settings_file, tmp_path, db_id=db_id)
    before = _keys(storage)
    monkeypatch.setenv("APP_PATHS__DATABASE", str(path))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("不可在檢查身分標記之前詢問"))
    for extra in (["--yes"], []):
        assert invites.main(["--settings", str(settings_file), "delete-data", "--id", str(vid), *extra]) == 4
    assert _keys(storage) == before
    with other() as s:  # 錯的 DB 也沒有被改
        assert s.get(Owner, vid) is not None and len(s.exec(select(Book)).all()) == 2
    err = capsys.readouterr().err
    assert "什麼都沒刪" in err and instance.bucket_instance_id(storage) in err


def test_delete_data_refuses_when_bucket_marker_missing(invites, settings_file, capsys):
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    _owner(sessions, "admin", is_admin=True)
    a = _owner_with_data(sessions, storage, "小明")
    storage.delete(instance.MARKER_KEY)
    before = _keys(storage)
    assert invites.main(["--settings", str(settings_file), "delete-data", "--id", str(a["owner"]), "--yes"]) == 4
    assert _keys(storage) == before
    assert "--init-instance" in capsys.readouterr().err


class _MarkerUnreadable:
    """包住真的 storage；讀標記時丟非「不存在」的錯誤（例如 R2 403 或逾時）。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get(self, key):
        if key.endswith("_instance.json"):
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "Forbidden"}}, "GetObject")
        return self._inner.get(key)


def test_marker_read_error_exits_5(storage_gc, invites, settings_file, monkeypatch, capsys):
    _, sessions, storage = _env(settings_file)
    _owner(sessions, "admin", is_admin=True)
    a = _owner_with_data(sessions, storage, "小明")
    before = _keys(storage)
    for mod in (storage_gc, invites):
        real = mod.make_storage
        monkeypatch.setattr(mod, "make_storage", lambda s, real=real: _MarkerUnreadable(real(s)))
    args = ["--settings", str(settings_file)]
    assert storage_gc.main(args) == 5
    assert storage_gc.main(args + ["--init-instance", "--yes"]) == 5
    assert invites.main(args + ["delete-data", "--id", str(a["owner"]), "--yes"]) == 5
    assert _keys(storage) == before
    err = capsys.readouterr().err
    assert "AccessDenied" in err and "Traceback" not in err


def _unmarked_nonempty(settings_file, n=15):
    """空的新 DB＋既有、沒有標記的 storage（books/ 有 n 個物件），經過一次後端啟動（拒絕認領）。"""
    from fastapi.testclient import TestClient

    from app.config import load_settings
    from app.main import create_app
    from app.services import instance
    from app.storage import make_storage
    from tests.fake_engine import FakeEngine

    s = load_settings(settings_file)
    storage = make_storage(s)
    for gid in range(1, n + 1):
        storage.put(storage.generation_image(1, gid), b"live")
    app = create_app(s, engine=FakeEngine(), start_worker=False)
    with TestClient(app):
        pass
    sessions = app.state.sessions
    with sessions() as sess:
        assert instance.db_instance_id(sess) is None
    assert instance.bucket_instance_id(storage) is None
    return sessions, storage


def test_empty_db_unmarked_storage_gc_and_delete_data_exit_4(storage_gc, invites, settings_file, capsys):
    sessions, storage = _unmarked_nonempty(settings_file)
    oid, _ = _owner(sessions, "new", is_admin=True)
    _bulk_gens(sessions, _book(sessions, oid), 15)  # 新 DB 長大了：book 1、gens 1..15 沒有圖
    v, _ = _owner(sessions, "v")
    _book(sessions, v)
    before = _keys(storage)
    args = ["--settings", str(settings_file)]
    assert storage_gc.main(args) == 4
    assert invites.main(args + ["delete-data", "--id", str(oid), "--yes", "--force-admin"]) == 4
    assert _keys(storage) == before
    assert "--init-instance" in capsys.readouterr().err


def test_init_instance_both_missing_requires_typed_bucket_name(storage_gc, settings_file, monkeypatch, capsys):
    from app.services import instance

    sessions, storage = _unmarked_nonempty(settings_file)
    label = instance.storage_label(storage)
    args = ["--settings", str(settings_file), "--init-instance"]
    monkeypatch.setattr("builtins.input", lambda *_: "nope")
    assert storage_gc.main(args) == 1  # 取消
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(EOFError()))
    assert storage_gc.main(args) == 1  # 沒有互動式輸入＝取消
    with sessions() as s:
        assert instance.db_instance_id(s) is None
    assert instance.bucket_instance_id(storage) is None
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or label)
    capsys.readouterr()
    assert storage_gc.main(args) == 0
    assert label in prompts[0]
    with sessions() as s:
        db_id = instance.db_instance_id(s)
    assert db_id and instance.bucket_instance_id(storage) == db_id
    assert db_id in capsys.readouterr().out
    before = _keys(storage)
    assert storage_gc.main(["--settings", str(settings_file), "--dry-run"]) == 0  # 一致了，GC 可以執行
    assert _keys(storage) == before


def test_init_instance_both_missing_with_yes(storage_gc, settings_file, monkeypatch):
    from app.services import instance

    sessions, storage = _unmarked_nonempty(settings_file)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("--yes 不詢問"))
    assert storage_gc.main(["--settings", str(settings_file), "--init-instance", "--yes"]) == 0
    with sessions() as s:
        assert instance.db_instance_id(s) == instance.bucket_instance_id(storage)


def test_init_instance_rejects_dry_run(storage_gc, settings_file):
    from app.services import instance

    sessions, storage = _unmarked_nonempty(settings_file)
    assert storage_gc.main(["--settings", str(settings_file), "--init-instance", "--dry-run", "--yes"]) == 2
    with sessions() as s:
        assert instance.db_instance_id(s) is None
    assert instance.bucket_instance_id(storage) is None


def test_adopt_bucket_id_when_db_id_missing(storage_gc, settings_file, monkeypatch, capsys):
    """DB 沒有 id、storage 有標記（降級或還原 0007 之前的備份）：--init-instance --adopt-bucket-id 把它寫進 DB。"""
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    bucket_id = instance.bucket_instance_id(storage)
    with sessions() as s:
        instance.delete_db_instance_id(s)
    args = ["--settings", str(settings_file)]
    assert storage_gc.main(args) == 4
    assert "--adopt-bucket-id" in capsys.readouterr().err
    assert storage_gc.main(args + ["--init-instance", "--yes"]) == 4  # 沒有 --adopt-bucket-id 不寫
    assert "--adopt-bucket-id" in capsys.readouterr().err
    assert storage_gc.main(args + ["--adopt-bucket-id"]) == 2  # 只能搭配 --init-instance
    monkeypatch.setattr("builtins.input", lambda *_: "nope")
    assert storage_gc.main(args + ["--init-instance", "--adopt-bucket-id"]) == 1
    with sessions() as s:
        assert instance.db_instance_id(s) is None
    monkeypatch.setattr("builtins.input", lambda *_: instance.storage_label(storage))
    assert storage_gc.main(args + ["--init-instance", "--adopt-bucket-id"]) == 0
    with sessions() as s:
        assert instance.db_instance_id(s) == bucket_id
    assert instance.bucket_instance_id(storage) == bucket_id
    assert storage_gc.main(args) == 0


def test_adopt_bucket_id_never_overwrites(storage_gc, settings_file):
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    with sessions() as s:
        db_id = instance.db_instance_id(s)
    instance.write_bucket_instance_id(storage, "b" * 32)  # 兩邊不同
    args = ["--settings", str(settings_file), "--init-instance", "--adopt-bucket-id", "--yes"]
    assert storage_gc.main(args) == 4
    with sessions() as s:
        assert instance.db_instance_id(s) == db_id
    assert instance.bucket_instance_id(storage) == "b" * 32
    storage.delete(instance.MARKER_KEY)  # storage 沒有標記：沒有 id 可認領
    assert storage_gc.main(args) == 4
    assert instance.bucket_instance_id(storage) is None
    with sessions() as s:
        instance.delete_db_instance_id(s)  # 兩邊都沒有：--adopt-bucket-id 也不建立
    assert storage_gc.main(args) == 4
    with sessions() as s:
        assert instance.db_instance_id(s) is None
    assert instance.bucket_instance_id(storage) is None


def test_init_instance_never_overwrites_differing_marker_with_yes(storage_gc, settings_file):
    from app.services import instance

    _, sessions, storage = _env(settings_file)
    instance.write_bucket_instance_id(storage, "b" * 32)
    assert storage_gc.main(["--settings", str(settings_file), "--init-instance", "--yes"]) == 4
    assert instance.bucket_instance_id(storage) == "b" * 32
    with sessions() as s:
        instance.delete_db_instance_id(s)
    assert storage_gc.main(["--settings", str(settings_file), "--init-instance", "--yes"]) == 4
    assert instance.bucket_instance_id(storage) == "b" * 32
    with sessions() as s:
        assert instance.db_instance_id(s) is None


def _run_fake_main(monkeypatch, settings_file, storage, capsys):
    monkeypatch.setenv("APP_SETTINGS_FILE", str(settings_file))
    started = []
    monkeypatch.setattr(fake_backend, "create_demo_app", lambda *a, **k: started.append(a) or object())
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    rc = fake_backend.main(["--storage", str(storage)])
    return rc, started, capsys.readouterr().err


def test_fake_backend_refuses_real_storage(settings_file, tmp_path, monkeypatch, capsys):
    from app.config import load_settings

    real = load_settings(settings_file).paths.storage_dir
    (real / "app.db").write_bytes(b"")
    # 1. 設定裡的 storage_dir
    rc, started, err = _run_fake_main(monkeypatch, settings_file, real, capsys)
    assert rc == 2 and not started and "假後端拒絕啟動" in err and str(real.resolve()) in err
    # 2. 沒有標記檔、但有 app.db 的其他資料夾
    other = tmp_path / "other"
    other.mkdir()
    (other / "app.db").write_bytes(b"")
    rc, started, err = _run_fake_main(monkeypatch, settings_file, other, capsys)
    assert rc == 2 and not started and "假後端拒絕啟動" in err
    # 3. 有標記檔的資料夾可以；新資料夾會建立標記檔
    (other / ".fake-backend").write_text("")
    rc, started, _ = _run_fake_main(monkeypatch, settings_file, other, capsys)
    assert rc == 0 and started
    fresh = tmp_path / "fresh"
    rc, started, _ = _run_fake_main(monkeypatch, settings_file, fresh, capsys)
    assert rc == 0 and (fresh / ".fake-backend").exists()


def test_fake_backend_refuses_database_parent(settings_file, tmp_path, monkeypatch, capsys):
    import yaml

    cfg = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    db_dir = tmp_path / "dbdir"
    db_dir.mkdir()
    cfg["paths"]["database"] = str(db_dir / "app.db")
    settings_file.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    rc, started, err = _run_fake_main(monkeypatch, settings_file, db_dir, capsys)
    assert rc == 2 and not started and "假後端拒絕啟動" in err
