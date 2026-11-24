"""頁面的兩個素材欄位（背景／角色動作）與「最新一張可用的 AI 圖」。

`auto_select`（頁面流程選圖）與 `auto_target`（還原成自動畫的那張）共用 newest_usable_ai，
排除上傳圖與重畫主角的候選（request_json 的 candidate）的條件只在這裡寫一次。
auto_cleanup 頁的 AI 背景在自動清理排隊／執行中時也不是候選（不選沒清過的那張；清理失敗後才又是候選）。
"""

from __future__ import annotations

from sqlalchemy import case, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from app.models import Generation, GenKind, GenStatus
from app.services.gen_request import AUTO_CLEANUP, CANDIDATE
from app.services.page_compose import usable_conditions

SLOTS = {
    GenKind.BACKGROUND: ("background_generation_id", "auto_background_generation_id"),
    GenKind.POSE: ("pose_generation_id", "auto_pose_generation_id"),
}
UPLOAD_ENGINE = "upload"  # 上傳的背景（api/compose.upload_background）：使用者的選擇，不是自動選圖的候選


def not_upload():
    """SQL 條件：不是上傳的（engine 還沒寫入，或不是 UPLOAD_ENGINE）。"""
    return or_(col(Generation.engine).is_(None), col(Generation.engine) != UPLOAD_ENGINE)


def _json_true(rj, key: str):
    """SQL：request_json 是合法 JSON 而且 key 是 JSON true（同 gen_request._flag 的 `is True`）；
    用 case 先檢查 json_valid，壞掉的 JSON 不會丟錯，當成沒有。"""
    flag = case((func.json_valid(rj) == 1, func.json_type(rj, f"$.{key}")), else_=None)
    return func.coalesce(flag, "") == "true"


def not_candidate():
    """SQL 條件：不是重畫主角的候選。"""
    return ~_json_true(col(Generation.request_json), CANDIDATE)


def not_awaiting_auto_cleanup():
    """SQL 條件：沒有排隊中／執行中的自動清理以它為來源（auto_cleanup 頁剛畫好、還沒清的背景不選）。"""
    child = aliased(Generation)
    return ~(
        select(child.id)
        .where(child.source_generation_id == Generation.id,
               col(child.status).in_([GenStatus.QUEUED, GenStatus.RUNNING]),
               _json_true(child.request_json, AUTO_CLEANUP))
        .correlate(Generation)
        .exists()
    )


def newest_usable_ai(session: Session, page_id: int, kind: GenKind) -> int | None:
    """這頁最新一張能用的 AI 圖（不含上傳的、重畫主角的候選、等著自動清理的背景）的 id；沒有回 None。"""
    return session.exec(
        select(Generation.id)
        .where(col(Generation.page_id) == page_id, *usable_conditions(kind), not_upload(), not_candidate(),
               not_awaiting_auto_cleanup())
        .order_by(col(Generation.id).desc())
    ).first()
