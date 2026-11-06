from pathlib import Path

import pytest
import yaml

from app.config import PROJECT_ROOT, SettingsError, load_settings


def test_load_settings_resolves_and_validates(settings_file: Path):
    s = load_settings(settings_file)
    assert s.comfy.url == "http://comfy.test:8188"
    assert s.comfy.ws_url == "ws://comfy.test:8188/ws"
    assert s.paths.database.is_absolute()


def test_env_overrides_yaml(settings_file: Path, monkeypatch):
    monkeypatch.setenv("APP_COMFY__URL", "http://other:9000/")
    monkeypatch.setenv("APP_COMFY__RUN_TIMEOUT_S", "42")
    s = load_settings(settings_file)
    assert s.comfy.url == "http://other:9000"
    assert s.comfy.run_timeout_s == 42
    # 沒被覆寫的巢狀欄位仍來自 YAML
    assert s.comfy.models_root.name == "models"


def test_settings_file_env(settings_file: Path, monkeypatch):
    monkeypatch.setenv("APP_SETTINGS_FILE", str(settings_file))
    assert load_settings().comfy.url == "http://comfy.test:8188"


def test_relative_paths_resolve_against_project_root(tmp_path: Path):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({
        "comfy": {"models_root": "models_x"},
        "paths": {"workflows_dir": "comfy_workflows", "storage_dir": "storage", "database": "storage/app.db"},
    }))
    s = load_settings(p, check_paths=False)
    assert s.paths.workflows_dir == PROJECT_ROOT / "comfy_workflows"
    assert s.comfy.models_root == PROJECT_ROOT / "models_x"


def test_missing_paths_listed_together(tmp_path: Path):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({
        "comfy": {"models_root": str(tmp_path / "nope"), "install_dir": str(tmp_path)},
        "paths": {"workflows_dir": str(tmp_path / "wf"), "storage_dir": str(tmp_path / "st"),
                  "database": str(tmp_path / "db" / "a.db")},
    }))
    with pytest.raises(SettingsError) as ei:
        load_settings(p)
    text = str(ei.value)
    assert len(ei.value.problems) == 5
    for key in ("models_root", "install_dir", "workflows_dir", "storage_dir", "database"):
        assert key in text


def test_missing_file_and_bad_values(tmp_path: Path):
    with pytest.raises(SettingsError, match="settings.example.yaml"):
        load_settings(tmp_path / "missing.yaml")
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"comfy": {"url": "ftp://x"}, "paths": {}}))
    with pytest.raises(SettingsError) as ei:
        load_settings(p)
    text = str(ei.value)
    assert "comfy.url" in text and "comfy.models_root" in text and "paths.workflows_dir" in text


def test_example_settings_is_valid_schema():
    s = load_settings(PROJECT_ROOT / "config" / "settings.example.yaml", check_paths=False)
    assert s.comfy.url.startswith("http")
