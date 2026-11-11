"""book.produce_started_at：最近一輪「開始做這本書／繼續做」的開始時間（B 整本書的進度用）

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11 00:00:06

只新增一個可為 null 的欄位；既有的書都是還沒按過（null）。downgrade 移除它。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models


revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('book') as b:
        b.add_column(sa.Column('produce_started_at', app.models.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('book', recreate="always") as b:
        b.drop_column('produce_started_at')
