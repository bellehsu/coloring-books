"""book.shared_at：分享給其他受邀使用者（null＝不分享）

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11 00:00:02

只新增一個可為 null 的欄位；既有的書都是不分享。downgrade 移除它。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('book') as b:
        b.add_column(sa.Column('shared_at', app.models.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('book', recreate="always") as b:
        b.drop_column('shared_at')
