"""page.history_cursor 與 page.pending_user_strokes：C 處理一頁的復原／重做（Phase 13 Task 4）

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-11 00:00:07

只新增兩個可為 null 的欄位；既有頁面都是「在最新狀態、沒有等著寫回的筆畫」（null）。
downgrade 移除它們（page 重建時以 table_kwargs 保留 AUTOINCREMENT，同 0006／0011）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTOINC = {"sqlite_autoincrement": True}


def upgrade() -> None:
    with op.batch_alter_table('page', table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('history_cursor', sa.Integer(), nullable=True))
        b.add_column(sa.Column('pending_user_strokes', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('page', recreate="always", table_kwargs=_AUTOINC) as b:
        b.drop_column('pending_user_strokes')
        b.drop_column('history_cursor')
