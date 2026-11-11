"""邀請制：owner／owner_session／usage、book.owner_id、generation.discarded_at；移除構圖草圖欄位

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11 00:00:00

升級順序：
1. 建 owner、owner_session、usage；
2. book 加 owner_id（先可為 NULL）；若已有書，建一個 admin owner（隨機 token 的 hash，沒有人知道原始 token；
   要連結時用 invites CLI 另發）並把所有書歸給它；沒有書就不建 owner；
3. book.owner_id 改 NOT NULL，加索引；
4. 已丟棄的生成紀錄回填 discarded_at（generation 沒有 updated_at：用 finished_at，沒有就用 created_at）；
5. 移除 page.sketch_path、page.controlnet_strength（Phase 9 起停用）。
downgrade 反向執行；移除的兩個欄位以舊預設值重建（sketch_path NULL、controlnet_strength 0.4），舊值無法復原。
沿用 0004／0005 的 batch 寫法（page、generation 保留 AUTOINCREMENT）。
"""
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

import app.models


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTOINC = {"sqlite_autoincrement": True}


def upgrade() -> None:
    op.create_table(
        'owner',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=False),
        sa.Column('daily_generation_cap', sa.Integer(), nullable=False),
        sa.Column('created_at', app.models.UTCDateTime(), nullable=False),
        sa.Column('revoked_at', app.models.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_table(
        'owner_session',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('session_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', app.models.UTCDateTime(), nullable=False),
        sa.Column('last_seen', app.models.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['owner.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_hash'),
    )
    op.create_index('ix_owner_session_owner_id', 'owner_session', ['owner_id'], unique=False)
    op.create_table(
        'usage',
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('generations', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['owner.id']),
        sa.PrimaryKeyConstraint('owner_id', 'day'),
    )

    with op.batch_alter_table('book') as b:
        b.add_column(sa.Column('owner_id', sa.Integer(), nullable=True))

    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM book")).scalar():
        owner = sa.table(
            'owner',
            sa.column('name', sa.String), sa.column('token_hash', sa.String), sa.column('is_admin', sa.Boolean),
            sa.column('daily_generation_cap', sa.Integer), sa.column('created_at', sa.DateTime),
        )
        token_hash = hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest()
        conn.execute(owner.insert().values(
            name="admin", token_hash=token_hash, is_admin=True, daily_generation_cap=60,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        admin_id = conn.execute(sa.text("SELECT id FROM owner WHERE token_hash = :h"), {"h": token_hash}).scalar()
        conn.execute(sa.text("UPDATE book SET owner_id = :o"), {"o": admin_id})

    with op.batch_alter_table('book') as b:
        b.alter_column('owner_id', existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key('fk_book_owner_id_owner', 'owner', ['owner_id'], ['id'])
        b.create_index('ix_book_owner_id', ['owner_id'], unique=False)

    with op.batch_alter_table('generation', table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('discarded_at', app.models.UTCDateTime(), nullable=True))
    conn.execute(sa.text(
        "UPDATE generation SET discarded_at = COALESCE(finished_at, created_at) WHERE discarded = 1"
    ))

    with op.batch_alter_table('page', table_kwargs=_AUTOINC) as b:
        b.drop_column('sketch_path')
        b.drop_column('controlnet_strength')


def downgrade() -> None:
    # recreate="always"：只用 ALTER TABLE ADD COLUMN 時，舊列不會實際寫入預設值，PRAGMA integrity_check 會報 NULL
    with op.batch_alter_table('page', recreate="always", table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('controlnet_strength', sa.Float(), nullable=False, server_default='0.4'))
        b.add_column(sa.Column('sketch_path', sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    with op.batch_alter_table('generation', table_kwargs=_AUTOINC) as b:
        b.drop_column('discarded_at')

    with op.batch_alter_table('book') as b:
        b.drop_index('ix_book_owner_id')
        b.drop_constraint('fk_book_owner_id_owner', type_='foreignkey')
        b.drop_column('owner_id')

    op.drop_table('usage')
    op.drop_index('ix_owner_session_owner_id', table_name='owner_session')
    op.drop_table('owner_session')
    op.drop_table('owner')
