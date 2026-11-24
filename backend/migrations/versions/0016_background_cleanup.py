"""page.auto_cleanup / cleanup_prompt、generation.source_generation_id（背景清理，2026-11-24）

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-24 00:00:00

page：auto_cleanup NOT NULL 布林（server default false，既有頁面都不自動清理）、cleanup_prompt 可為 NULL 的文字。
generation：source_generation_id 可為 NULL 的整數，外鍵指向 generation.id、ON DELETE SET NULL
（清理後的背景記得它是從哪一張清出來的；來源被刪就變 NULL）。
SQLite 加外鍵欄要重建表；page／generation 重建時都以 table_kwargs 保留 AUTOINCREMENT（同 0015）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTOINC = {"sqlite_autoincrement": True}


def upgrade() -> None:
    with op.batch_alter_table('page', table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('auto_cleanup', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('cleanup_prompt', sa.String(), nullable=True))
    with op.batch_alter_table('generation', recreate="always", table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('source_generation_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_generation_source_generation_id', 'generation', ['source_generation_id'], ['id'],
                             ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('generation', recreate="always", table_kwargs=_AUTOINC) as b:
        b.drop_constraint('fk_generation_source_generation_id', type_='foreignkey')
        b.drop_column('source_generation_id')
    with op.batch_alter_table('page', recreate="always", table_kwargs=_AUTOINC) as b:
        b.drop_column('cleanup_prompt')
        b.drop_column('auto_cleanup')
