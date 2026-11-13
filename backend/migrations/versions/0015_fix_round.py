"""page.outline_fill / redraw_background / redraw_pose、book 改用 AUTOINCREMENT（2026-11-13 修正輪）

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-13 00:00:00

page 加三個 NOT NULL 布林欄位，server default false：既有頁面都是 false（維持以前的行為），
新建頁面的 outline_fill 預設值 True 由 ORM 給（models.Page）。
book 重建成 AUTOINCREMENT（同 0003 對 page／generation）：刪除書之後 id 永不沿用。
downgrade 反向：page 移除三欄（重建時以 table_kwargs 保留 AUTOINCREMENT），book 重建回一般 rowid。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTOINC = {"sqlite_autoincrement": True}


def upgrade() -> None:
    with op.batch_alter_table('page', table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('outline_fill', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('redraw_background', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('redraw_pose', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('book', recreate="always", table_kwargs={"sqlite_autoincrement": True}):
        pass


def downgrade() -> None:
    with op.batch_alter_table('book', recreate="always", table_kwargs={"sqlite_autoincrement": False}):
        pass
    with op.batch_alter_table('page', recreate="always", table_kwargs=_AUTOINC) as b:
        b.drop_column('redraw_pose')
        b.drop_column('redraw_background')
        b.drop_column('outline_fill')
