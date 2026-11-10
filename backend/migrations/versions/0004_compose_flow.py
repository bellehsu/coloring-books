"""頁面合成流程：generation.mask_path、page 的背景／動作／位置欄位

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09 00:00:00

GenKind 以成員名稱存成 VARCHAR（無 CHECK 限制；SQLite 不強制長度），新增的
BACKGROUND／POSE／COMPOSITE 可直接寫入；kind 長度仍從 9 放寬到 10（'BACKGROUND'），使 schema 與模型一致。沿用 0003 的 batch 寫法。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("generation", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.add_column(sa.Column('mask_path', sa.String(), nullable=True))
        b.alter_column('kind', existing_type=sa.VARCHAR(length=9), type_=sa.VARCHAR(length=10), existing_nullable=False)
    with op.batch_alter_table("page", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.add_column(sa.Column('background_generation_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('pose_generation_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('placement_json', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("page", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.drop_column('placement_json')
        b.drop_column('pose_generation_id')
        b.drop_column('background_generation_id')
    with op.batch_alter_table("generation", table_kwargs={"sqlite_autoincrement": True}) as b:
        b.drop_column('mask_path')
        b.alter_column('kind', existing_type=sa.VARCHAR(length=10), type_=sa.VARCHAR(length=9), existing_nullable=False)
