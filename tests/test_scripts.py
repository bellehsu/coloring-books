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


def test_fake_backend_full_flow(settings_file, tmp_path, monkeypatch):
    """假後端可啟動：角色 → 頁面候選 → 選圖 → QA 有缺口 → 自動補線後 0；資料寫在 --storage，不碰設定的 storage。"""
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
                      json={"description": "a cat", "count": 4}).status_code == 202
        chars = _wait_succeeded(c, book["id"], "character", 4)
        assert c.post(f"/api/books/{book['id']}/character", json={"generation_id": chars[0]["id"]}).status_code == 200

        page = c.post(f"/api/books/{book['id']}/pages", json={"scene_prompt": "park"}).json()
        assert c.post(f"/api/pages/{page['id']}/candidates", json={"count": 1}).status_code == 202
        gen = _wait_succeeded(c, book["id"], "page", 1)[0]
        assert (gen["width"], gen["height"]) == (864, 1216)
        assert c.post(f"/api/pages/{page['id']}/select", json={"generation_id": gen["id"]}).status_code == 200

        qa = c.get(f"/api/pages/{page['id']}/qa").json()
        assert qa["gap_count"] >= 1
        fixed = c.post(f"/api/pages/{page['id']}/lineart/autofix", json={"generation_id": gen["id"]}).json()
        assert fixed["autofix_added"] >= 1 and fixed["gap_count"] == 0
    assert (storage / "app.db").is_file()
    assert not (tmp_path / "storage" / "app.db").exists()


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
