"""一鍵做書（Phase 13）：page 的 attention／自動選圖欄位、page_task、page_history、修線分成 auto／user 兩層

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11 00:00:05

升級：
1. page 加四個可為 NULL 的欄位（attention、attention_detail、auto_background_generation_id、auto_pose_generation_id）；
   既有頁面都是 NULL。
2. 建 page_task、page_history。
3. lineartedit 加 layer（AUTO／USER），主鍵改成 (generation_id, layer)。SQLite 改不了主鍵，所以重建資料表：
   建新表、複製（既有的修線都是使用者畫的：layer＝USER）、刪舊表、改名、重建索引。
downgrade 反向執行；兩層併回一列（auto 在前、user 在後，與渲染順序相同，印出來的線稿不變）。
併起來超過舊版 PUT 上限（500 筆／20000 點）時從最前面丟，保留最新的筆畫（見 _cap）。
沿用 0006 的 batch 寫法（page 重建時以 table_kwargs 保留 AUTOINCREMENT）。
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

import app.models


revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTOINC = {"sqlite_autoincrement": True}  # page 重建時保留 AUTOINCREMENT（同 0006）
_TMP = '_lineartedit_0011'
_COPY_COLS = 'generation_id, page_id, strokes_json, updated_at'


def _create_lineartedit(name: str, *, layered: bool) -> None:
    cols = [sa.Column('generation_id', sa.Integer(), nullable=False)]
    if layered:
        cols.append(sa.Column('layer', sa.Enum('AUTO', 'USER', name='strokelayer'), nullable=False,
                              server_default='USER'))
    cols += [
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('strokes_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', app.models.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['generation_id'], ['generation.id']),
        sa.ForeignKeyConstraint(['page_id'], ['page.id']),
        sa.PrimaryKeyConstraint(*(('generation_id', 'layer') if layered else ('generation_id',))),
    ]
    op.create_table(name, *cols)


def _swap_in(tmp: str) -> None:
    op.drop_index('ix_lineartedit_page_id', table_name='lineartedit')
    op.drop_table('lineartedit')
    op.rename_table(tmp, 'lineartedit')
    op.create_index('ix_lineartedit_page_id', 'lineartedit', ['page_id'], unique=False)


def upgrade() -> None:
    with op.batch_alter_table('page', table_kwargs=_AUTOINC) as b:
        b.add_column(sa.Column('attention', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        b.add_column(sa.Column('attention_detail', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        b.add_column(sa.Column('auto_background_generation_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('auto_pose_generation_id', sa.Integer(), nullable=True))

    op.create_table(
        'page_task',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', app.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', app.models.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['page_id'], ['page.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_page_task_page_id', 'page_task', ['page_id'], unique=False)
    op.create_index('ix_page_task_status', 'page_task', ['status'], unique=False)
    op.create_table(
        'page_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('snapshot_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', app.models.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['page_id'], ['page.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('page_id', 'seq', name='uq_page_history_page_seq'),
    )

    _create_lineartedit(_TMP, layered=True)
    op.execute(
        f"INSERT INTO {_TMP} (generation_id, layer, page_id, strokes_json, updated_at) "
        f"SELECT generation_id, 'USER', page_id, strokes_json, updated_at FROM lineartedit"
    )
    _swap_in(_TMP)


# 0010 的 PUT /lineart/edits 上限（app.api.schemas 在這個版本的值；遷移不 import 會變動的程式碼）
_OLD_MAX_STROKES = 500
_OLD_MAX_TOTAL_POINTS = 20000


def _cap(strokes: list) -> list:
    """併成一列後可能超過舊版 PUT 的上限，頁面就再也存不了修線。從最前面丟（auto 在前，所以先丟最舊的
    自動補線），保留最新的筆畫：使用者畫的優先留下，補線之後重按「自動補線」就能補回。"""
    start, points = 0, sum(len(s.get('points', [])) if isinstance(s, dict) else 0 for s in strokes)
    while start < len(strokes) and (len(strokes) - start > _OLD_MAX_STROKES or points > _OLD_MAX_TOTAL_POINTS):
        s = strokes[start]
        points -= len(s.get('points', [])) if isinstance(s, dict) else 0
        start += 1
    return strokes[start:]


def _merged_rows(conn) -> list[dict]:
    """每張圖併成一列：auto 的筆畫在前、user 在後（見 _cap）；只有一層就沿用那一層的原文。"""
    rows = conn.execute(sa.text(
        "SELECT generation_id, layer, page_id, strokes_json, updated_at FROM lineartedit "
        "ORDER BY generation_id, layer"  # 'AUTO' < 'USER'
    )).mappings().all()
    merged: dict[int, dict] = {}
    for r in rows:
        cur = merged.get(r['generation_id'])
        if cur is None:
            merged[r['generation_id']] = {k: r[k] for k in ('generation_id', 'page_id', 'strokes_json', 'updated_at')}
            continue
        first, second = json.loads(cur['strokes_json'] or '[]'), json.loads(r['strokes_json'] or '[]')
        if not first:
            cur['strokes_json'] = r['strokes_json']
        elif second:
            cur['strokes_json'] = json.dumps(_cap(first + second), separators=(',', ':'), ensure_ascii=False)
        cur['updated_at'] = max(cur['updated_at'], r['updated_at'])
    return list(merged.values())


def downgrade() -> None:
    conn = op.get_bind()
    rows = _merged_rows(conn)
    _create_lineartedit(_TMP, layered=False)
    if rows:
        conn.execute(
            sa.text(f"INSERT INTO {_TMP} ({_COPY_COLS}) VALUES "
                    "(:generation_id, :page_id, :strokes_json, :updated_at)"),
            rows,
        )
    _swap_in(_TMP)

    op.drop_table('page_history')
    op.drop_index('ix_page_task_status', table_name='page_task')
    op.drop_index('ix_page_task_page_id', table_name='page_task')
    op.drop_table('page_task')

    with op.batch_alter_table('page', recreate="always", table_kwargs=_AUTOINC) as b:
        b.drop_column('auto_pose_generation_id')
        b.drop_column('auto_background_generation_id')
        b.drop_column('attention_detail')
        b.drop_column('attention')
