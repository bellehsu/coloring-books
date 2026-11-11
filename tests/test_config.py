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


def test_queue_defaults_and_env_override(settings_file: Path, monkeypatch):
    # 舊設定檔（沒有 queue 段落）仍可載入，使用預設值
    assert load_settings(settings_file).queue.retry_backoff_s == 10.0
    monkeypatch.setenv("APP_QUEUE__RETRY_BACKOFF_S", "0")
    assert load_settings(settings_file).queue.retry_backoff_s == 0.0


def test_settings_ignore_max_wait(settings_file: Path):
    data = yaml.safe_load(settings_file.read_text())
    data["queue"] = {"max_wait_s": 180, "retry_backoff_s": 3}
    settings_file.write_text(yaml.safe_dump(data))
    s = load_settings(settings_file)
    assert s.queue.retry_backoff_s == 3.0
    assert not hasattr(s.queue, "max_wait_s")


def test_create_app_worker_has_no_family_scheduling(settings_file: Path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.fake_engine import FakeEngine

    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app):
        assert not hasattr(app.state.worker, "max_wait_s")
        assert not hasattr(app.state.worker, "last_family")


# ---------- Phase 10：storage 設定 ----------

STORAGE_ENV = (
    "APP_STORAGE__BACKEND", "APP_STORAGE__R2__ENDPOINT", "APP_STORAGE__R2__BUCKET",
    "APP_STORAGE__R2__ACCESS_KEY", "APP_STORAGE__R2__SECRET_KEY", "APP_STORAGE__SIGNING_KEY",
    "APP_STORAGE__SIGNED_URL_TTL_S", "APP_STORAGE__CACHE_DIR", "APP_STORAGE__CACHE_MAX_BYTES",
)


@pytest.fixture
def clean_storage_env(monkeypatch):
    for k in STORAGE_ENV:
        monkeypatch.delenv(k, raising=False)


def test_settings_storage_defaults(settings_file: Path, clean_storage_env, monkeypatch):
    s = load_settings(settings_file)
    assert s.storage.backend == "local"
    assert s.storage.signed_url_ttl_s == 120
    assert s.storage.cache_max_bytes == 2 * 1024**3
    assert s.storage.r2.access_key is None and s.storage.r2.secret_key is None
    assert s.storage.signing_key is None

    monkeypatch.setenv("APP_STORAGE__BACKEND", "r2")
    monkeypatch.setenv("APP_STORAGE__R2__ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("APP_STORAGE__R2__BUCKET", "books")
    monkeypatch.setenv("APP_STORAGE__R2__ACCESS_KEY", "AK-plain")
    monkeypatch.setenv("APP_STORAGE__R2__SECRET_KEY", "SK-super-secret-value")
    monkeypatch.setenv("APP_STORAGE__SIGNING_KEY", "SIGN-super-secret-value")
    s = load_settings(settings_file)
    assert s.storage.backend == "r2"
    assert s.storage.r2.endpoint == "https://acct.r2.cloudflarestorage.com"
    assert s.storage.r2.bucket == "books"
    assert s.storage.r2.access_key.get_secret_value() == "AK-plain"
    assert s.storage.r2.secret_key.get_secret_value() == "SK-super-secret-value"
    for text in (repr(s), str(s), repr(s.storage), s.model_dump_json()):
        assert "SK-super-secret-value" not in text
        assert "SIGN-super-secret-value" not in text
        assert "AK-plain" not in text


def test_settings_storage_rejects_credentials_in_yaml(settings_file: Path, clean_storage_env):
    data = yaml.safe_load(settings_file.read_text())
    data["storage"] = {"backend": "r2", "r2": {"endpoint": "https://x", "bucket": "b",
                                                 "access_key": "AK-in-yaml", "secret_key": "SK-in-yaml"}}
    settings_file.write_text(yaml.safe_dump(data))
    with pytest.raises(SettingsError) as ei:
        load_settings(settings_file)
    text = str(ei.value)
    assert "APP_STORAGE__R2__ACCESS_KEY" in text and "APP_STORAGE__R2__SECRET_KEY" in text
    assert "SK-in-yaml" not in text and "AK-in-yaml" not in text

    data["storage"] = {"signing_key": "SIGN-in-yaml"}
    settings_file.write_text(yaml.safe_dump(data))
    with pytest.raises(SettingsError) as ei:
        load_settings(settings_file)
    assert "APP_STORAGE__SIGNING_KEY" in str(ei.value) and "SIGN-in-yaml" not in str(ei.value)


def test_settings_storage_yaml_and_relative_cache_dir(settings_file: Path, clean_storage_env):
    data = yaml.safe_load(settings_file.read_text())
    data["storage"] = {"backend": "local", "signed_url_ttl_s": 60, "cache_dir": "tmp_cache",
                       "cache_max_bytes": 1000}
    settings_file.write_text(yaml.safe_dump(data))
    s = load_settings(settings_file)
    assert s.storage.signed_url_ttl_s == 60
    assert s.storage.cache_dir == PROJECT_ROOT / "tmp_cache"
    assert s.storage.cache_max_bytes == 1000
    data["storage"] = {"backend": "s3"}
    settings_file.write_text(yaml.safe_dump(data))
    with pytest.raises(SettingsError, match="storage.backend"):
        load_settings(settings_file)


def test_example_settings_has_storage_section():
    raw = yaml.safe_load((PROJECT_ROOT / "config" / "settings.example.yaml").read_text(encoding="utf-8"))
    st = raw["storage"]
    assert st["backend"] == "local"
    assert st["signed_url_ttl_s"] == 120
    assert "access_key" not in (st.get("r2") or {}) and "secret_key" not in (st.get("r2") or {})


def test_auth_settings_defaults_and_env(settings_file: Path, monkeypatch):
    s = load_settings(settings_file)
    assert s.auth.dev_owner == "admin"  # conftest 的 dev_owner fixture
    assert s.auth.cookie_secure is False
    monkeypatch.setenv("APP_AUTH__DEV_OWNER", "  ")  # 空白＝不啟用
    monkeypatch.setenv("APP_AUTH__COOKIE_SECURE", "true")
    s = load_settings(settings_file)
    assert s.auth.dev_owner is None and s.auth.cookie_secure is True
    from app.config import AuthSettings

    assert AuthSettings().dev_owner is None  # 程式預設不啟用（雲端安全）


def test_example_settings_has_auth_section():
    """範本不啟用 dev_owner（複製到雲端也安全）；本機開發自己設（README、dev.env.example 有寫）。"""
    raw = yaml.safe_load((PROJECT_ROOT / "config" / "settings.example.yaml").read_text(encoding="utf-8"))
    assert raw["auth"] == {"dev_owner": None, "cookie_secure": False, "invite_base_url": None}
    assert "APP_AUTH__DEV_OWNER=admin" in (PROJECT_ROOT / "scripts" / "dev.env.example").read_text(encoding="utf-8")


# ---------- make_storage ----------


def test_make_storage_local_generates_signing_key_once(settings_file: Path, clean_storage_env):
    import stat

    from app.storage import LocalStorage, make_storage

    s = load_settings(settings_file)
    st = make_storage(s)
    assert isinstance(st, LocalStorage)
    key_file = s.paths.storage_dir / ".signing_key"
    assert key_file.is_file()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    first = key_file.read_bytes()
    url = st.signed_url("books/1/a.png", 120)
    # 第二次建立沿用同一把金鑰：重啟後舊網址仍有效
    st2 = make_storage(load_settings(settings_file))
    assert key_file.read_bytes() == first
    from urllib.parse import parse_qs, urlparse

    q = parse_qs(urlparse(url).query)
    assert st2.verify("books/1/a.png", q["exp"][0], q["sig"][0])


def test_make_storage_tightens_loose_signing_key_permissions(settings_file: Path, clean_storage_env):
    import stat

    from app.storage import make_storage

    s = load_settings(settings_file)
    key_file = s.paths.storage_dir / ".signing_key"
    key_file.write_text("a" * 64)
    key_file.chmod(0o644)
    make_storage(s)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert key_file.read_text() == "a" * 64


def test_make_storage_signing_key_from_env(settings_file: Path, clean_storage_env, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from app.storage import LocalStorage, make_storage

    monkeypatch.setenv("APP_STORAGE__SIGNING_KEY", "env-signing-key")
    s = load_settings(settings_file)
    st = make_storage(s)
    assert not (s.paths.storage_dir / ".signing_key").exists()
    q = parse_qs(urlparse(st.signed_url("books/1/a.png", 120)).query)
    assert LocalStorage(s.paths.storage_dir, b"env-signing-key").verify("books/1/a.png", q["exp"][0], q["sig"][0])


def test_make_storage_r2_is_cached(settings_file: Path, clean_storage_env, monkeypatch, tmp_path):
    pytest.importorskip("moto")
    from moto import mock_aws

    from app.storage import CachedStorage, R2Storage, make_storage

    monkeypatch.setenv("APP_STORAGE__BACKEND", "r2")
    monkeypatch.setenv("APP_STORAGE__R2__ENDPOINT", "https://s3.amazonaws.com")
    monkeypatch.setenv("APP_STORAGE__R2__BUCKET", "test-bucket")
    monkeypatch.setenv("APP_STORAGE__R2__ACCESS_KEY", "AK")
    monkeypatch.setenv("APP_STORAGE__R2__SECRET_KEY", "SK")
    monkeypatch.setenv("APP_STORAGE__CACHE_DIR", str(tmp_path / "objcache"))
    with mock_aws():
        st = make_storage(load_settings(settings_file))
        assert isinstance(st, CachedStorage)
        assert isinstance(st.inner, R2Storage)
        assert st.cache_dir == tmp_path / "objcache"
        assert st.max_bytes == 2 * 1024**3


def test_make_storage_r2_missing_settings_listed(settings_file: Path, clean_storage_env, monkeypatch):
    from app.storage import make_storage

    monkeypatch.setenv("APP_STORAGE__BACKEND", "r2")
    monkeypatch.setenv("APP_STORAGE__R2__SECRET_KEY", "SK-hidden")
    with pytest.raises(SettingsError) as ei:
        make_storage(load_settings(settings_file))
    text = str(ei.value)
    for name in ("storage.r2.endpoint", "storage.r2.bucket", "APP_STORAGE__R2__ACCESS_KEY"):
        assert name in text
    assert "APP_STORAGE__R2__SECRET_KEY" not in text  # 有設的不列
    assert "SK-hidden" not in text


def test_create_app_uses_make_storage(settings_file: Path, clean_storage_env):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.storage import LocalStorage
    from tests.fake_engine import FakeEngine

    s = load_settings(settings_file)
    app = create_app(s, engine=FakeEngine(), start_worker=False, startup_preflight=False)
    with TestClient(app):
        assert isinstance(app.state.storage, LocalStorage)
        assert (s.paths.storage_dir / ".signing_key").is_file()
