"""page.action_prompt：每頁的角色動作文字（「故事」步驟）

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10 00:00:00

NOT NULL、server default ''，既有頁面自動得到空字串。沿用 0004 的 batch 寫法（保留 AUTOINCREMENT）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("page", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.add_column(sa.Column('action_prompt', sa.String(), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table("page", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.drop_column('action_prompt')
