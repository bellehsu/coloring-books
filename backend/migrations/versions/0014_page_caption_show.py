"""page.caption_show_zh / caption_show_en：每頁選要印哪幾行圖下方的字（Phase 13b）

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-12 00:00:00

兩個 NOT NULL 布林欄位，server default true：既有頁面都是「兩行都印」，和以前一樣。
兩個都關＝圖下方留一條空白的文字帶（services/captions.py）。
downgrade 移除它們（page 重建時以 table_kwargs 保留 AUTOINCREMENT，同 0006／0011／0013）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTOINC = {"sqlite_autoincrement": True}


def upgrade() -> None:
    with op.batch_alter_table('page', table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('caption_show_zh', sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column('caption_show_en', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table('page', recreate="always", table_kwargs=_AUTOINC) as b:
        b.drop_column('caption_show_en')
        b.drop_column('caption_show_zh')
