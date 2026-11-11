"""page.caption_zh / caption_en：印在頁面下方的中文與英文短字

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11 00:00:04

只新增兩個可為 null 的欄位；既有的頁面都是 NULL（沒有文字，輸出與以前完全相同）。downgrade 移除它們
（page 重建時以 table_kwargs 保留 AUTOINCREMENT，同 0006／0011）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('page') as b:
        b.add_column(sa.Column('caption_zh', sa.String(), nullable=True))
        b.add_column(sa.Column('caption_en', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('page', recreate="always", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.drop_column('caption_en')
        b.drop_column('caption_zh')
