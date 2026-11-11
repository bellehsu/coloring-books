"""owner.token_encrypted：加密保存的邀請 token（管理頁可再次顯示連結）

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-11 00:00:03

只新增一個可為 null 的欄位；既有的邀請是 NULL（連結無法再顯示，用 scripts/invites.py rotate 換發）。
downgrade 移除它。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('owner') as b:
        b.add_column(sa.Column('token_encrypted', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('owner', recreate="always") as b:
        b.drop_column('token_encrypted')
