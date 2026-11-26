"""page.include_character（不放主角的頁，2026-11-26）

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-26 00:00:00

page：include_character NOT NULL 布林（server default true，既有頁面都放主角）。
page 重建時以 table_kwargs 保留 AUTOINCREMENT（同 0015、0016）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTOINC = {"sqlite_autoincrement": True}


def upgrade() -> None:
    with op.batch_alter_table('page', table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('include_character', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table('page', recreate="always", table_kwargs=_AUTOINC) as b:
        b.drop_column('include_character')
