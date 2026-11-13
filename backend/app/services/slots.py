"""頁面的兩個素材欄位（背景／角色動作）與「最新一張可用的 AI 圖」。

`auto_select`（頁面流程選圖）與 `auto_target`（還原成自動畫的那張）共用 newest_usable_ai，
排除上傳圖與重畫主角的候選（request_json 的 candidate）的條件只在這裡寫一次。
"""

from __future__ import annotations

from sqlalchemy import case, func, or_
from sqlmodel import Session, col, select

from app.models import Generation, GenKind
from app.services.gen_request import CANDIDATE
from app.services.page_compose import usable_conditions

SLOTS = {
    GenKind.BACKGROUND: ("background_generation_id", "auto_background_generation_id"),
    GenKind.POSE: ("pose_generation_id", "auto_pose_generation_id"),
}
UPLOAD_ENGINE = "upload"  # 上傳的背景（api/compose.upload_background）：使用者的選擇，不是自動選圖的候選


def not_upload():
    """SQL 條件：不是上傳的（engine 還沒寫入，或不是 UPLOAD_ENGINE）。"""
    return or_(col(Generation.engine).is_(None), col(Generation.engine) != UPLOAD_ENGINE)


def not_candidate():
    """SQL 條件：不是重畫主角的候選。request_json 是合法 JSON 而且 candidate 是 JSON true 才算候選
    （同 pipeline.is_candidate 的 `is True`）；用 case 先檢查 json_valid，壞掉的 JSON 不會丟錯，當成不是候選。"""
    rj = col(Generation.request_json)
    flag = case((func.json_valid(rj) == 1, func.json_type(rj, f"$.{CANDIDATE}")), else_=None)
    return func.coalesce(flag, "") != "true"


def newest_usable_ai(session: Session, page_id: int, kind: GenKind) -> int | None:
    """這頁最新一張能用的 AI 圖（不含上傳的、不含重畫主角的候選）的 id；沒有回 None。"""
    return session.exec(
        select(Generation.id)
        .where(col(Generation.page_id) == page_id, *usable_conditions(kind), not_upload(), not_candidate())
        .order_by(col(Generation.id).desc())
    ).first()
