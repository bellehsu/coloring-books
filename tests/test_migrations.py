from alembic import command
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlmodel import SQLModel

from app.db import BASELINE_REVISION, alembic_config, init_db, make_engine


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
    init_db(eng)
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT title FROM book").scalar() == "舊書"
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == head_revision(eng)
    init_db(eng)
