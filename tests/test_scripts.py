"""scripts/sync_comfy_model_paths.py 與 scripts/check_env.py。"""

import importlib.util
import sys
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
    req = GenerationRequest(kind=GenKind.PAGE, prompt="p", negative_prompt="", seed=5, width=864, height=1216)
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

    req = GenerationRequest(kind=GenKind(kind), prompt="p", negative_prompt="", seed=1, width=size[0], height=size[1])
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

    req = GenerationRequest(kind=GenKind.POSE, prompt="p", negative_prompt="", seed=1, width=832, height=1216,
                            variation="facing left")
    res = asyncio.run(fake_backend.DemoEngine(delay_s=0).generate(req))
    img = Image.open(io.BytesIO(res.image))
    assert img.size == Image.open(io.BytesIO(res.extra_images["mask"])).size == (832, 1216)
    assert res.params["variation"] == "facing left"
    assert not {"pose_mode", "structure_strength", "has_structure_image"} & set(res.params)
    req.variation = None
    assert "variation" not in asyncio.run(fake_backend.DemoEngine(delay_s=0).generate(req)).params


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
        assert pose["pose_mode"] == "precise"
        assert pose["cutout_url"]
        assert c.get(pose["cutout_url"]).status_code == 200
        assert c.post(f"/api/pages/{page['id']}/pose", json={"generation_id": pose["id"]}).status_code == 200

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

        deadline = time.monotonic() + 10
        while True:
            pages = {p["id"]: p for p in c.get(f"/api/books/{book['id']}/pages").json()}
            if pages[p1["id"]]["background_generation_id"] and pages[p1["id"]]["pose_generation_id"]:
                break
            assert time.monotonic() < deadline, pages
            time.sleep(0.05)
        assert pages[p1["id"]]["compose_pending"] is True

        sel = c.post(f"/api/pages/{p1['id']}/compose").json()["selected_generation_id"]
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
        assert app.state.preflight is None
        book = c.post("/api/books", json={"title": "示範", "paper_size": "a4", "print_mode": "print_shop"}).json()
        c.post(f"/api/books/{book['id']}/character/candidates", json={"description": "a cat", "count": 1})
        char = _wait_succeeded(c, book["id"], "character", 1)[0]
        c.post(f"/api/books/{book['id']}/character", json={"generation_id": char["id"]})
        page = c.post(f"/api/books/{book['id']}/pages", json={"scene_prompt": "park"}).json()
        res = c.post(f"/api/pages/{page['id']}/poses", json={"action": "waving one hand"})
        assert res.status_code == 202, res.text
        assert _wait_succeeded(c, book["id"], "pose", 1)[0]["pose_mode"] == "precise"
    assert seen == []
    # 對照：同一個 ComfyUI 下真的跑 preflight，workflow 會被判定不能用（上面的結果不是巧合）
    app2 = app_main.create_app(fake_backend.demo_settings(tmp_path / "s2", load_settings(settings_file)),
                               engine=fake_backend.DemoEngine(delay_s=0), start_worker=False)
    with TestClient(app2):
        report = app2.state.preflight
        assert report is not None and report.comfy_reachable and not report.ok
    assert "/object_info" in seen


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
