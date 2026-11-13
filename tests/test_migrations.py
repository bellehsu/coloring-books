import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from app.db import BASELINE_REVISION, alembic_config, init_db, make_engine

AUTOINCREMENT_TABLES = ("page", "generation")


def table_sql(conn, name: str) -> str:
    return conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).scalar()


def head_revision(eng) -> str:
    return ScriptDirectory.from_config(alembic_config(eng)).get_current_head()


def test_fresh_db_upgrades_to_head(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    assert {"book", "page", "generation", "alembic_version"} <= set(inspect(eng).get_table_names())


def test_schema_matches_models(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"render_as_batch": True}), SQLModel.metadata)
    assert diff == []


def test_legacy_db_is_stamped_and_upgraded(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ALLOW_LEGACY_UPGRADE", "1")  # 刻意就地升級有書的舊庫
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), BASELINE_REVISION)
    with eng.begin() as c:
        c.exec_driver_sql("DROP TABLE alembic_version")
        c.exec_driver_sql(
            "INSERT INTO book (title, paper_size, print_mode, created_at, updated_at) "
            "VALUES ('舊書', 'A4', 'HOME', '2026-11-06 00:00:00', '2026-11-06 00:00:00')"
        )
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, controlnet_strength, status, created_at, updated_at) "
            "VALUES (1, 1, '', 0.4, 'DRAFT', '2026-11-06 00:00:00', '2026-11-06 00:00:00')"
        )
    init_db(eng)
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT title FROM book").scalar() == "舊書"
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == head_revision(eng)
        row = c.exec_driver_sql("SELECT line_width_px, line_detail, qa_note FROM page").one()
        assert tuple(row) == (11, "STANDARD", "")
        row = c.exec_driver_sql(
            "SELECT background_generation_id, pose_generation_id, placement_json FROM page").one()
        assert tuple(row) == (None, None, None)
    init_db(eng)


def test_fresh_db_page_and_generation_use_autoincrement(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    with eng.connect() as c:
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in table_sql(c, t), t


def _seed_0002(eng) -> None:
    """模擬真實資料庫：停在 0002 且有資料（含 id 空洞、修線、索引）。"""
    command.upgrade(alembic_config(eng), "0002")
    ts = "'2026-11-06 00:00:00'"
    with eng.begin() as c:
        c.exec_driver_sql(
            f"INSERT INTO book (id, title, paper_size, print_mode, created_at, updated_at) "
            f"VALUES (1, '舊書', 'A4', 'HOME', {ts}, {ts})"
        )
        for pid, pos in ((1, 0), (3, 1)):
            c.exec_driver_sql(
                "INSERT INTO page (id, book_id, position, scene_prompt, controlnet_strength, status, "
                "line_detail, line_width_px, qa_note, selected_generation_id, created_at, updated_at) "
                f"VALUES ({pid}, 1, {pos}, '公園{pid}', 0.4, 'PASSED', 'DETAILED', 12, 'ok', {pid * 10}, {ts}, {ts})"
            )
        for gid, pid in ((10, 1), (30, 3), (31, 3)):
            c.exec_driver_sql(
                "INSERT INTO generation (id, book_id, page_id, kind, status, discarded, user_text, prompt, "
                "negative_prompt, seed, width, height, request_json, image_path, created_at) "
                f"VALUES ({gid}, 1, {pid}, 'PAGE', 'SUCCEEDED', 0, 'u', 'p', 'n', {gid}, 864, 1216, '{{}}', "
                f"'books/1/generations/{gid}.png', {ts})"
            )
        c.exec_driver_sql(
            f"INSERT INTO lineartedit (generation_id, page_id, strokes_json, updated_at) VALUES (30, 3, '[1]', {ts})"
        )


def _dump(c) -> dict:
    return {
        t: [tuple(r) for r in c.exec_driver_sql(f"SELECT * FROM {t} ORDER BY 1").all()]
        for t in ("book", "page", "generation", "lineartedit")
    }


def test_upgrade_from_0002_rebuilds_with_autoincrement_and_keeps_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ALLOW_LEGACY_UPGRADE", "1")  # 刻意就地升級有書的舊庫
    eng = make_engine(tmp_path / "a.db")
    _seed_0002(eng)
    with eng.connect() as c:
        before = _dump(c)
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" not in table_sql(c, t)
    command.upgrade(alembic_config(eng), "0003")
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0003"
        assert _dump(c) == before
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in table_sql(c, t), t
        insp = inspect(c)
        assert {i["name"] for i in insp.get_indexes("page")} == {"ix_page_book_id"}
        assert {i["name"] for i in insp.get_indexes("generation")} == {
            "ix_generation_book_id", "ix_generation_page_id", "ix_generation_status"}
        fks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys("generation")}
        assert fks == {(("book_id",), "book"), (("page_id",), "page")}
        assert [(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys("page")] == [
            (("book_id",), "book")]
        lfks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys("lineartedit")}
        assert lfks == {(("generation_id",), "generation"), (("page_id",), "page")}
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    # 刪掉 id 最大的頁面與生成紀錄後再新增：不可沿用
    with eng.begin() as c:
        c.exec_driver_sql("DELETE FROM lineartedit")
        c.exec_driver_sql("DELETE FROM generation WHERE page_id = 3")
        c.exec_driver_sql("DELETE FROM page WHERE id = 3")
        ts = "'2026-11-07 00:00:00'"
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, controlnet_strength, status, line_detail, "
            f"line_width_px, qa_note, created_at, updated_at) VALUES (1, 1, '', 0.4, 'DRAFT', 'STANDARD', 11, '', {ts}, {ts})"
        )
        c.exec_driver_sql(
            "INSERT INTO generation (book_id, page_id, kind, status, discarded, user_text, prompt, negative_prompt, "
            f"seed, width, height, request_json, created_at) VALUES (1, 1, 'PAGE', 'QUEUED', 0, 'u', 'p', 'n', 1, 8, 8, '{{}}', {ts})"
        )
        assert c.exec_driver_sql("SELECT max(id) FROM page").scalar() == 4
        assert c.exec_driver_sql("SELECT max(id) FROM generation").scalar() == 32
    init_db(eng)  # 冪等


def test_upgrade_to_head_keeps_generation_and_accepts_new_kinds(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ALLOW_LEGACY_UPGRADE", "1")  # 刻意就地升級有書的舊庫
    eng = make_engine(tmp_path / "a.db")
    _seed_0002(eng)
    init_db(eng)
    ts = "'2026-11-09 00:00:00'"
    with eng.begin() as c:
        row = c.exec_driver_sql("SELECT id, kind, image_path, mask_path FROM generation WHERE id = 10").one()
        assert tuple(row) == (10, "PAGE", "books/1/generations/10.png", None)
        for kind in ("BACKGROUND", "POSE", "COMPOSITE"):
            c.exec_driver_sql(
                "INSERT INTO generation (book_id, page_id, kind, status, discarded, user_text, prompt, "
                f"negative_prompt, seed, width, height, request_json, created_at) VALUES (1, 1, '{kind}', "
                f"'QUEUED', 0, 'u', 'p', 'n', 1, 8, 8, '{{}}', {ts})"
            )
        assert c.exec_driver_sql("SELECT count(*) FROM generation WHERE kind IN ('BACKGROUND','POSE','COMPOSITE')").scalar() == 3
    # 0004 重建資料表後仍保留 AUTOINCREMENT（刪掉的 id 不可沿用）
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == head_revision(eng)
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in table_sql(c, t), t


def test_migration_0005_adds_action_prompt(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    _seed_0002(eng)
    command.upgrade(alembic_config(eng), "0004")
    with eng.connect() as c:
        assert "action_prompt" not in {col["name"] for col in inspect(c).get_columns("page")}
    command.upgrade(alembic_config(eng), "0005")
    with eng.connect() as c:
        cols = {col["name"]: col for col in inspect(c).get_columns("page")}
        assert "action_prompt" in cols and cols["action_prompt"]["nullable"] is False
        assert c.exec_driver_sql("SELECT count(*) FROM page").scalar() > 0
        assert c.exec_driver_sql("SELECT DISTINCT action_prompt FROM page").all() == [("",)]
        assert "AUTOINCREMENT" in table_sql(c, "page")
    command.downgrade(alembic_config(eng), "0004")
    with eng.connect() as c:
        assert "action_prompt" not in {col["name"] for col in inspect(c).get_columns("page")}


def _seed_0005(eng) -> None:
    """Phase 9 資料庫的複本：停在 0005，有書、已丟棄的生成紀錄、舊的構圖草圖欄位。"""
    _seed_0002(eng)
    command.upgrade(alembic_config(eng), "0005")
    with eng.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO book (id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (2, '第二本', 'A4', 'HOME', '2026-11-07 00:00:00', '2026-11-07 00:00:00')"
        )
        c.exec_driver_sql("UPDATE page SET sketch_path = 'books/1/pages/1/sketch.png', controlnet_strength = 0.7 WHERE id = 1")
        # 30：已完成後丟棄 → 用 finished_at；31：沒有 finished_at → 退回 created_at；10：未丟棄
        c.exec_driver_sql(
            "UPDATE generation SET discarded = 1, finished_at = '2026-11-08 12:00:00.000000' WHERE id = 30")
        c.exec_driver_sql("UPDATE generation SET discarded = 1, created_at = '2026-11-06 09:30:00.000000' WHERE id = 31")


def _cols(c, table) -> dict:
    return {col["name"]: col for col in inspect(c).get_columns(table)}


def test_0006_upgrade_on_copy_of_phase9_db(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    _seed_0005(eng)
    with eng.connect() as c:
        before_pages = c.exec_driver_sql("SELECT id, book_id, position, scene_prompt FROM page ORDER BY id").all()
    command.upgrade(alembic_config(eng), "0006")
    with eng.connect() as c:
        tables = set(inspect(c).get_table_names())
        assert {"owner", "owner_session", "usage"} <= tables
        owners = c.exec_driver_sql("SELECT id, name, token_hash, is_admin, daily_generation_cap, revoked_at FROM owner").all()
        assert len(owners) == 1
        oid, name, token_hash, is_admin, cap, revoked = owners[0]
        assert (name, is_admin, cap, revoked) == ("admin", 1, 60, None)
        assert len(token_hash) == 64 and int(token_hash, 16) >= 0  # sha256 hex，沒有人知道原始 token
        assert c.exec_driver_sql("SELECT created_at FROM owner").scalar() is not None
        assert c.exec_driver_sql("SELECT DISTINCT owner_id FROM book").all() == [(oid,)]
        assert c.exec_driver_sql("SELECT count(*) FROM book").scalar() == 2
        assert _cols(c, "book")["owner_id"]["nullable"] is False
        # discarded_at：只回填已丟棄的
        got = dict(c.exec_driver_sql("SELECT id, discarded_at FROM generation").all())
        assert got[10] is None
        assert got[30].startswith("2026-11-08 12:00:00")
        assert got[31].startswith("2026-11-06 09:30:00")
        # 兩個構圖草圖欄位已移除，其餘頁面資料不變
        page_cols = _cols(c, "page")
        assert "sketch_path" not in page_cols and "controlnet_strength" not in page_cols
        assert c.exec_driver_sql("SELECT id, book_id, position, scene_prompt FROM page ORDER BY id").all() == before_pages
        # 外鍵、索引、AUTOINCREMENT 都還在
        insp = inspect(c)
        fk = lambda t: {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys(t)}  # noqa: E731
        assert fk("book") == {(("owner_id",), "owner")}
        assert fk("page") == {(("book_id",), "book")}
        assert fk("generation") == {(("book_id",), "book"), (("page_id",), "page")}
        assert fk("lineartedit") == {(("generation_id",), "generation"), (("page_id",), "page")}
        assert fk("owner_session") == {(("owner_id",), "owner")}
        assert fk("usage") == {(("owner_id",), "owner")}
        assert {i["name"] for i in insp.get_indexes("page")} == {"ix_page_book_id"}
        assert {i["name"] for i in insp.get_indexes("generation")} == {
            "ix_generation_book_id", "ix_generation_page_id", "ix_generation_status"}
        assert {i["name"] for i in insp.get_indexes("book")} == {"ix_book_owner_id"}
        assert insp.get_pk_constraint("usage")["constrained_columns"] == ["owner_id", "day"]
        uniq = lambda t: {tuple(i["column_names"]) for i in insp.get_indexes(t) if i["unique"]} | {  # noqa: E731
            tuple(u["column_names"]) for u in insp.get_unique_constraints(t)}
        assert ("token_hash",) in uniq("owner")
        assert ("session_hash",) in uniq("owner_session")
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in table_sql(c, t), t
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    init_db(eng)  # 冪等：不會再建第二個 admin
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM owner").scalar() == 1


def test_0006_book_owner_id_is_not_null(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    with pytest.raises(IntegrityError):
        with eng.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO book (title, paper_size, print_mode, created_at, updated_at) "
                "VALUES ('x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
            )


def test_0006_empty_db(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0005")
    command.upgrade(alembic_config(eng), "0006")
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM owner").scalar() == 0
        assert _cols(c, "book")["owner_id"]["nullable"] is False


def test_0006_downgrade_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    _seed_0005(eng)
    with eng.connect() as c:
        books_before = c.exec_driver_sql("SELECT * FROM book ORDER BY id").all()
        gen_cols_before = set(_cols(c, "generation"))
    command.upgrade(alembic_config(eng), "0006")
    command.downgrade(alembic_config(eng), "0005")
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0005"
        tables = set(inspect(c).get_table_names())
        assert not ({"owner", "owner_session", "usage"} & tables)
        assert "owner_id" not in _cols(c, "book")
        assert c.exec_driver_sql("SELECT * FROM book ORDER BY id").all() == books_before
        assert set(_cols(c, "generation")) == gen_cols_before  # discarded_at 已移除
        page_cols = _cols(c, "page")
        assert page_cols["sketch_path"]["nullable"] is True
        assert page_cols["controlnet_strength"]["nullable"] is False
        # 移除的資料無法復原：舊欄位回到預設值
        assert c.exec_driver_sql("SELECT DISTINCT sketch_path, controlnet_strength FROM page").all() == [(None, 0.4)]
        assert {i["name"] for i in inspect(c).get_indexes("book")} == set()
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in table_sql(c, t), t
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    # 0005 的舊程式仍能新增頁面（不帶 controlnet_strength）
    with eng.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, status, line_detail, line_width_px, qa_note, created_at, updated_at) "
            "VALUES (1, 5, '', 'DRAFT', 'STANDARD', 11, '', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    command.upgrade(alembic_config(eng), "head")
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM owner").scalar() == 1


def test_0007_app_meta_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0006")
    command.upgrade(alembic_config(eng), "0007")
    with eng.begin() as c:
        assert "app_meta" in set(inspect(c).get_table_names())
        c.exec_driver_sql("INSERT INTO app_meta (key, value) VALUES ('storage_instance_id', 'abc')")
    command.downgrade(alembic_config(eng), "0006")
    with eng.connect() as c:
        assert "app_meta" not in set(inspect(c).get_table_names())
    command.upgrade(alembic_config(eng), "head")


def test_0008_book_shared_at_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0007")
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    command.upgrade(alembic_config(eng), "0008")
    with eng.connect() as c:
        col = _cols(c, "book")["shared_at"]
        assert col["nullable"] is True
        assert c.exec_driver_sql("SELECT shared_at FROM book").scalar() is None  # 既有的書預設不分享
    command.downgrade(alembic_config(eng), "0007")
    with eng.connect() as c:
        assert "shared_at" not in _cols(c, "book")
        assert c.exec_driver_sql("SELECT title FROM book").scalar() == "x"
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")


def test_0009_owner_token_encrypted_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0008")
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
    command.upgrade(alembic_config(eng), "0009")
    with eng.connect() as c:
        assert _cols(c, "owner")["token_encrypted"]["nullable"] is True
        assert c.exec_driver_sql("SELECT token_encrypted FROM owner").scalar() is None  # 既有的邀請：無法再顯示連結
    command.downgrade(alembic_config(eng), "0008")
    with eng.connect() as c:
        assert "token_encrypted" not in _cols(c, "owner")
        assert c.exec_driver_sql("SELECT name FROM owner").scalar() == "o"
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")


def test_0010_page_captions_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0009")
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, action_prompt, status, line_detail, line_width_px, "
            "qa_note, created_at, updated_at) VALUES (1, 0, 's', 'a', 'DRAFT', 'STANDARD', 11, '', "
            "'2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    command.upgrade(alembic_config(eng), "0010")
    with eng.connect() as c:
        cols = _cols(c, "page")
        assert cols["caption_zh"]["nullable"] is True and cols["caption_en"]["nullable"] is True
        assert c.exec_driver_sql("SELECT caption_zh, caption_en FROM page").one() == (None, None)
    command.downgrade(alembic_config(eng), "0009")
    with eng.connect() as c:
        cols = _cols(c, "page")
        assert "caption_zh" not in cols and "caption_en" not in cols
        assert c.exec_driver_sql("SELECT scene_prompt FROM page").scalar() == "s"
        assert "AUTOINCREMENT" in table_sql(c, "page")  # downgrade 重建 page 時保留（刪掉的 id 不沿用）
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")


def _seed_0010(eng) -> None:
    """停在 0010：一頁、兩張合成圖（30 有修線、31 沒有）。"""
    command.upgrade(alembic_config(eng), "0010")
    ts = "'2026-11-11 00:00:00'"
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          f"VALUES ('o', 'h', 0, 60, {ts})")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            f"VALUES (1, 'x', 'A4', 'HOME', {ts}, {ts})"
        )
        c.exec_driver_sql(
            "INSERT INTO page (id, book_id, position, scene_prompt, action_prompt, status, line_detail, line_width_px, "
            f"qa_note, selected_generation_id, created_at, updated_at) VALUES (3, 1, 0, 's', 'a', 'PASSED', 'STANDARD', 11, "
            f"'', 30, {ts}, {ts})"
        )
        for gid in (30, 31):
            c.exec_driver_sql(
                "INSERT INTO generation (id, book_id, page_id, kind, status, discarded, user_text, prompt, "
                "negative_prompt, seed, width, height, request_json, created_at) "
                f"VALUES ({gid}, 1, 3, 'COMPOSITE', 'SUCCEEDED', 0, 'u', 'p', 'n', 1, 8, 8, '{{}}', {ts})"
            )
        c.exec_driver_sql(
            f"INSERT INTO lineartedit (generation_id, page_id, strokes_json, updated_at) VALUES (30, 3, '[1]', {ts})"
        )


def test_0011_oneclick_upgrade(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    _seed_0010(eng)
    command.upgrade(alembic_config(eng), "0011")
    with eng.connect() as c:
        cols = _cols(c, "page")
        for name in ("attention", "attention_detail", "auto_background_generation_id", "auto_pose_generation_id"):
            assert cols[name]["nullable"] is True, name
        assert c.exec_driver_sql(
            "SELECT attention, attention_detail, auto_background_generation_id, auto_pose_generation_id FROM page"
        ).one() == (None, None, None, None)
        # 既有的修線都是使用者畫的：layer＝USER，其餘不變
        assert c.exec_driver_sql(
            "SELECT generation_id, layer, page_id, strokes_json, updated_at FROM lineartedit").all() == [
            (30, "USER", 3, "[1]", "2026-11-11 00:00:00")]
        insp = inspect(c)
        assert insp.get_pk_constraint("lineartedit")["constrained_columns"] == ["generation_id", "layer"]
        assert {i["name"] for i in insp.get_indexes("lineartedit")} == {"ix_lineartedit_page_id"}
        fk = lambda t: {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys(t)}  # noqa: E731
        assert fk("lineartedit") == {(("generation_id",), "generation"), (("page_id",), "page")}
        assert fk("page_task") == {(("page_id",), "page")}
        assert fk("page_history") == {(("page_id",), "page")}
        assert set(_cols(c, "page_task")) == {"id", "page_id", "kind", "status", "error", "created_at", "updated_at"}
        assert set(_cols(c, "page_history")) == {"id", "page_id", "seq", "snapshot_json", "created_at"}
        assert _cols(c, "page_task")["error"]["nullable"] is True
        assert {i["name"] for i in insp.get_indexes("page_task")} == {"ix_page_task_page_id", "ix_page_task_status"}
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in table_sql(c, t), t
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    ts = "'2026-11-11 00:00:01'"
    with eng.begin() as c:
        # 同一張圖可以同時有 auto 與 user 兩層，但每層只有一列
        c.exec_driver_sql(
            f"INSERT INTO lineartedit (generation_id, layer, page_id, strokes_json, updated_at) VALUES (30, 'AUTO', 3, '[2]', {ts})")
        c.exec_driver_sql(
            "INSERT INTO page_task (page_id, kind, status, created_at, updated_at) "
            f"VALUES (3, 'pipeline', 'queued', {ts}, {ts})")
        c.exec_driver_sql(
            f"INSERT INTO page_history (page_id, seq, snapshot_json, created_at) VALUES (3, 1, '{{}}', {ts})")
    with pytest.raises(IntegrityError):
        with eng.begin() as c:
            c.exec_driver_sql(
                f"INSERT INTO lineartedit (generation_id, layer, page_id, strokes_json, updated_at) VALUES (30, 'AUTO', 3, '[]', {ts})")
    with pytest.raises(IntegrityError):  # 同一頁的 seq 不重複
        with eng.begin() as c:
            c.exec_driver_sql(
                f"INSERT INTO page_history (page_id, seq, snapshot_json, created_at) VALUES (3, 1, '{{}}', {ts})")
    init_db(eng)  # 已在 head：冪等


def test_0011_oneclick_downgrade_merges_auto_strokes(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    _seed_0010(eng)
    command.upgrade(alembic_config(eng), "0011")
    ts = "'2026-11-11 00:00:01'"
    with eng.begin() as c:
        c.exec_driver_sql(
            "UPDATE page SET attention = 'failed', attention_detail = '背景生成失敗', auto_background_generation_id = 31")
        c.exec_driver_sql("UPDATE lineartedit SET strokes_json = '[{\"u\":1}]'")
        # 30：auto＋user；31：只有 auto
        for gid, strokes in ((30, '[{\"a\":1},{\"a\":2}]'), (31, '[{\"a\":3}]')):
            c.exec_driver_sql(
                "INSERT INTO lineartedit (generation_id, layer, page_id, strokes_json, updated_at) "
                f"VALUES ({gid}, 'AUTO', 3, '{strokes}', {ts})")
        c.exec_driver_sql(
            "INSERT INTO page_task (page_id, kind, status, created_at, updated_at) "
            f"VALUES (3, 'pipeline', 'queued', {ts}, {ts})")
    command.downgrade(alembic_config(eng), "0010")
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0010"
        tables = set(inspect(c).get_table_names())
        assert not ({"page_task", "page_history"} & tables)
        cols = _cols(c, "page")
        assert not ({"attention", "attention_detail", "auto_background_generation_id", "auto_pose_generation_id"}
                    & set(cols))
        assert c.exec_driver_sql("SELECT scene_prompt, selected_generation_id FROM page").one() == ("s", 30)
        # 只剩一層：auto 在前、user 在後（與渲染順序相同），印出來的線稿不變
        assert "layer" not in _cols(c, "lineartedit")
        rows = c.exec_driver_sql("SELECT generation_id, page_id, strokes_json FROM lineartedit ORDER BY 1").all()
        assert [(g, p) for g, p, _ in rows] == [(30, 3), (31, 3)]
        import json
        assert json.loads(rows[0][2]) == [{"a": 1}, {"a": 2}, {"u": 1}]
        assert json.loads(rows[1][2]) == [{"a": 3}]
        insp = inspect(c)
        assert insp.get_pk_constraint("lineartedit")["constrained_columns"] == ["generation_id"]
        assert {i["name"] for i in insp.get_indexes("lineartedit")} == {"ix_lineartedit_page_id"}
        for t in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in table_sql(c, t), t
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT DISTINCT layer FROM lineartedit").all() == [("USER",)]


def test_0011_downgrade_caps_merged_strokes_to_old_put_limits(tmp_path):
    """兩層各自在上限內，併成一列可能超過舊 PUT 的上限（500 筆／20000 點）而讓頁面再也存不了修線：
    併好之後從最前面（最舊的 auto 筆畫）丟，保留最新的筆畫。"""
    import json

    from app.api.schemas import MAX_STROKES, MAX_TOTAL_POINTS

    eng = make_engine(tmp_path / "a.db")
    _seed_0010(eng)
    command.upgrade(alembic_config(eng), "0011")
    stroke = lambda tag, n=1: {"op": "draw", "width_px": 11, "points": [[0.1, 0.1]] * n, "t": tag}  # noqa: E731
    auto = [stroke(f"a{i}") for i in range(300)]
    user = [stroke(f"u{i}") for i in range(300)]
    auto31 = [stroke(f"b{i}", 100) for i in range(150)]  # 15000 點
    user31 = [stroke(f"v{i}", 100) for i in range(100)]  # 10000 點
    ts = "'2026-11-11 00:00:01'"
    with eng.begin() as c:
        c.exec_driver_sql("UPDATE lineartedit SET strokes_json = ?", (json.dumps(user),))
        c.exec_driver_sql(
            "INSERT INTO lineartedit (generation_id, layer, page_id, strokes_json, updated_at) "
            f"VALUES (30, 'AUTO', 3, ?, {ts}), (31, 'AUTO', 3, ?, {ts}), (31, 'USER', 3, ?, {ts})",
            (json.dumps(auto), json.dumps(auto31), json.dumps(user31)))
    command.downgrade(alembic_config(eng), "0010")
    with eng.connect() as c:
        got = {g: json.loads(t) for g, t in c.exec_driver_sql("SELECT generation_id, strokes_json FROM lineartedit")}
    assert len(got[30]) == MAX_STROKES
    assert got[30] == (auto + user)[-MAX_STROKES:]  # 丟掉最舊的 100 筆 auto，使用者的全部保留
    assert sum(len(s["points"]) for s in got[31]) <= MAX_TOTAL_POINTS
    assert got[31] == (auto31 + user31)[-200:]  # 20000 點＝最後 200 筆


def test_0012_book_produce_started_at_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0011")
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, action_prompt, status, line_detail, line_width_px, "
            "qa_note, created_at, updated_at) VALUES (1, 0, 's', 'a', 'DRAFT', 'STANDARD', 11, '', "
            "'2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    command.upgrade(alembic_config(eng), "0012")
    with eng.connect() as c:
        assert _cols(c, "book")["produce_started_at"]["nullable"] is True
        assert c.exec_driver_sql("SELECT produce_started_at FROM book").scalar() is None  # 既有的書：還沒按過
    command.downgrade(alembic_config(eng), "0011")
    with eng.connect() as c:
        assert "produce_started_at" not in _cols(c, "book")
        assert c.exec_driver_sql("SELECT title FROM book").scalar() == "x"
        assert c.exec_driver_sql("SELECT count(*) FROM page").scalar() == 1
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")


def test_0013_page_history_cursor_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0012")
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, action_prompt, status, line_detail, line_width_px, "
            "qa_note, created_at, updated_at) VALUES (1, 0, 's', 'a', 'DRAFT', 'STANDARD', 11, '', "
            "'2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    command.upgrade(alembic_config(eng), "0013")
    with eng.connect() as c:
        cols = _cols(c, "page")
        assert cols["history_cursor"]["nullable"] is True and cols["pending_user_strokes"]["nullable"] is True
        assert c.exec_driver_sql("SELECT history_cursor, pending_user_strokes FROM page").one() == (None, None)
    command.downgrade(alembic_config(eng), "0012")
    with eng.connect() as c:
        assert "history_cursor" not in _cols(c, "page") and "pending_user_strokes" not in _cols(c, "page")
        assert c.exec_driver_sql("SELECT scene_prompt FROM page").scalar() == "s"
        sql = c.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'page'").scalar()
        assert "AUTOINCREMENT" in sql
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")


def test_0014_page_caption_show_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0013")
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, action_prompt, caption_zh, status, line_detail, "
            "line_width_px, qa_note, created_at, updated_at) VALUES (1, 0, 's', 'a', '火車', 'DRAFT', 'STANDARD', 11, "
            "'', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    command.upgrade(alembic_config(eng), "0014")
    with eng.connect() as c:
        cols = _cols(c, "page")
        assert cols["caption_show_zh"]["nullable"] is False and cols["caption_show_en"]["nullable"] is False
        # 既有的頁面：兩行都印（和以前一樣）
        assert tuple(c.exec_driver_sql("SELECT caption_show_zh, caption_show_en FROM page").one()) == (1, 1)
        sql = c.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'page'").scalar()
        assert "AUTOINCREMENT" in sql
    with eng.begin() as c:  # 新的列沒給值也是兩行都印
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, action_prompt, status, line_detail, "
            "line_width_px, qa_note, created_at, updated_at) VALUES (1, 1, '', '', 'DRAFT', 'STANDARD', 11, "
            "'', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
        assert tuple(c.exec_driver_sql(
            "SELECT caption_show_zh, caption_show_en FROM page WHERE position = 1").one()) == (1, 1)
        with pytest.raises(IntegrityError):
            with c.begin_nested():
                c.exec_driver_sql("UPDATE page SET caption_show_zh = NULL")
    command.downgrade(alembic_config(eng), "0013")
    with eng.connect() as c:
        assert "caption_show_zh" not in _cols(c, "page") and "caption_show_en" not in _cols(c, "page")
        assert c.exec_driver_sql("SELECT caption_zh FROM page WHERE position = 0").scalar() == "火車"
        sql = c.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'page'").scalar()
        assert "AUTOINCREMENT" in sql
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")


def test_0015_fix_round_roundtrip(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), "0014")
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
        c.exec_driver_sql(
            "INSERT INTO page (book_id, position, scene_prompt, action_prompt, caption_zh, status, line_detail, "
            "line_width_px, qa_note, created_at, updated_at) VALUES (1, 0, 's', 'a', '火車', 'DRAFT', 'STANDARD', 11, "
            "'', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    command.upgrade(alembic_config(eng), "0015")
    with eng.begin() as c:
        cols = _cols(c, "page")
        for name in ("outline_fill", "redraw_background", "redraw_pose"):
            assert cols[name]["nullable"] is False
        assert tuple(c.exec_driver_sql("SELECT outline_fill, redraw_background, redraw_pose FROM page").one()) == (0, 0, 0)
        assert "AUTOINCREMENT" in table_sql(c, "book") and "AUTOINCREMENT" in table_sql(c, "page")
        assert c.exec_driver_sql("SELECT title FROM book").scalar() == "x"
        c.exec_driver_sql("DELETE FROM page WHERE book_id = 1")
        c.exec_driver_sql("DELETE FROM book WHERE id = 1")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'y', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
        assert c.exec_driver_sql("SELECT id FROM book").scalar() == 2
    command.downgrade(alembic_config(eng), "0014")
    with eng.connect() as c:
        cols = _cols(c, "page")
        assert not {"outline_fill", "redraw_background", "redraw_pose"} & set(cols)
        assert "AUTOINCREMENT" in table_sql(c, "page")
        assert "AUTOINCREMENT" not in table_sql(c, "book")
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    command.upgrade(alembic_config(eng), "head")


def test_fresh_db_book_uses_autoincrement(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    with eng.connect() as c:
        assert "AUTOINCREMENT" in table_sql(c, "book")


def test_orm_new_page_defaults(tmp_path):
    from sqlmodel import Session
    from app.models import Page

    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    with eng.begin() as c:
        c.exec_driver_sql("INSERT INTO owner (name, token_hash, is_admin, daily_generation_cap, created_at) "
                          "VALUES ('o', 'h', 0, 60, '2026-11-11 00:00:00')")
        c.exec_driver_sql(
            "INSERT INTO book (owner_id, title, paper_size, print_mode, created_at, updated_at) "
            "VALUES (1, 'x', 'A4', 'HOME', '2026-11-11 00:00:00', '2026-11-11 00:00:00')"
        )
    with Session(eng) as s:
        p = Page(book_id=1, position=0)
        s.add(p)
        s.commit()
        s.refresh(p)
        assert p.outline_fill is True and p.redraw_background is False and p.redraw_pose is False
