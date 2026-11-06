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
