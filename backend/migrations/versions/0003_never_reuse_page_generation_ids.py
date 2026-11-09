"""page / generation 改用 AUTOINCREMENT（刪除後 id 永不沿用）

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09 00:00:00

SQLite 無法 ALTER 出 AUTOINCREMENT，只能重建資料表：batch 模式建立暫存表、複製全部資料、
刪舊表再改名，索引與外鍵沿用反射結果。重建後 sqlite_sequence 從現有最大 id 起算。
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("page", "generation")


def _rebuild(autoincrement: bool) -> None:
    for name in TABLES:
        with op.batch_alter_table(
            name, recreate="always", table_kwargs={"sqlite_autoincrement": autoincrement}
        ):
            pass


def upgrade() -> None:
    _rebuild(True)


def downgrade() -> None:
    _rebuild(False)
