from alembic import command
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
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


def test_legacy_db_is_stamped_and_upgraded(tmp_path):
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
        assert "AUTOINCREMENT" not in table_sql(c, "book")


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


def test_upgrade_from_0002_rebuilds_with_autoincrement_and_keeps_rows(tmp_path):
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


def test_upgrade_to_head_keeps_generation_and_accepts_new_kinds(tmp_path):
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
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0005"
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
