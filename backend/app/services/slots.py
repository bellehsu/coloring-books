"""頁面的兩個素材欄位（背景／角色動作）與「最新一張可用的 AI 圖」。

`auto_select`（頁面流程選圖）與 `auto_target`（還原成自動畫的那張）共用 newest_usable_ai，
排除上傳圖的條件只在這裡寫一次。
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.models import Generation, GenKind
from app.services.page_compose import usable_conditions

SLOTS = {
    GenKind.BACKGROUND: ("background_generation_id", "auto_background_generation_id"),
    GenKind.POSE: ("pose_generation_id", "auto_pose_generation_id"),
}
UPLOAD_ENGINE = "upload"  # 上傳的背景（api/compose.upload_background）：使用者的選擇，不是自動選圖的候選


def not_upload():
    """SQL 條件：不是上傳的（engine 還沒寫入，或不是 UPLOAD_ENGINE）。"""
    return or_(col(Generation.engine).is_(None), col(Generation.engine) != UPLOAD_ENGINE)


def newest_usable_ai(session: Session, page_id: int, kind: GenKind) -> int | None:
    """這頁最新一張能用的 AI 圖（不含上傳的）的 id；沒有回 None。"""
    return session.exec(
        select(Generation.id)
        .where(col(Generation.page_id) == page_id, *usable_conditions(kind), not_upload())
        .order_by(col(Generation.id).desc())
    ).first()
