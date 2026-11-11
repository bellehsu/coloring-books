"""舊（Phase 9，0006 之前）資料庫的升級保護：有書的舊庫不會被 app 啟動或腳本默默升級（除非 APP_ALLOW_LEGACY_UPGRADE=1）。"""

import hashlib
import importlib.util
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db import LegacyUpgradeRefused, alembic_config, init_db, make_engine, schema_revisions
from app.main import create_app
from tests.fake_engine import FakeEngine
from tests.test_migrations import _seed_0005


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _no_flag(monkeypatch):
    monkeypatch.delenv("APP_ALLOW_LEGACY_UPGRADE", raising=False)


@pytest.fixture
def legacy_db(settings_file):
    """settings 指到的資料庫：Phase 9 的複本，停在 0005，有兩本書。"""
    path = load_settings(settings_file).paths.database
    eng = make_engine(path)
    _seed_0005(eng)
    eng.dispose()
    return path


def _cli():
    spec = importlib.util.spec_from_file_location(
        "invites_cli_guard", Path(__file__).resolve().parents[1] / "scripts" / "invites.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_init_db_refuses_legacy_db_with_books_and_leaves_file_unchanged(legacy_db):
    before = _sha(legacy_db)
    eng = make_engine(legacy_db)
    with pytest.raises(LegacyUpgradeRefused) as e:
        init_db(eng)
    assert "從 Phase 9 升級" in str(e.value) and "APP_ALLOW_LEGACY_UPGRADE=1" in str(e.value)
    eng.dispose()
    assert _sha(legacy_db) == before


def test_create_app_refuses_and_leaves_file_unchanged(settings_file, legacy_db):
    before = _sha(legacy_db)
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False, startup_preflight=False)
    with pytest.raises(LegacyUpgradeRefused):
        with TestClient(app):
            pass
    assert _sha(legacy_db) == before


def test_script_refuses_with_clear_message(settings_file, legacy_db, capsys):
    before = _sha(legacy_db)
    assert _cli().main(["--settings", str(settings_file), "list"]) == 2
    err = capsys.readouterr().err
    assert "從 Phase 9 升級" in err and "APP_ALLOW_LEGACY_UPGRADE=1" in err
    assert _sha(legacy_db) == before


def test_env_flag_allows_the_upgrade(legacy_db, monkeypatch):
    monkeypatch.setenv("APP_ALLOW_LEGACY_UPGRADE", "1")
    eng = make_engine(legacy_db)
    init_db(eng)
    cur, head = schema_revisions(eng)
    assert cur == head == "0009"


def test_pre_0006_db_without_books_still_upgrades(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0005")  # 沒有任何書
    init_db(eng)
    cur, head = schema_revisions(eng)
    assert cur == head


def test_phase2_baseline_db_with_books_is_refused(tmp_path):
    """沒有 alembic_version 的 Phase 2 舊庫（有 book 表）也一樣。"""
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0001")
    with eng.begin() as c:
        c.exec_driver_sql("DROP TABLE alembic_version")
        c.exec_driver_sql("INSERT INTO book (title, paper_size, print_mode, created_at, updated_at) "
                          "VALUES ('x', 'A4', 'HOME', '2026-11-07 00:00:00', '2026-11-07 00:00:00')")
    with pytest.raises(LegacyUpgradeRefused):
        init_db(eng)


def test_current_db_with_books_is_not_affected(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql("INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
                          "VALUES (1, 'x', 'A4', 'HOME', '2026-11-07 00:00:00', '2026-11-07 00:00:00')")
    init_db(eng)  # 已經是 0009：照常
