"""worker 成功後自動選用：把第一張成功的背景／角色動作放進頁面的空欄位。"""

import logging

from sqlmodel import Session, col, update

from app.models import Generation, GenKind, GenStatus, Page, utcnow

log = logging.getLogger(__name__)

_SLOT = {GenKind.BACKGROUND: "background_generation_id", GenKind.POSE: "pose_generation_id"}


def autoselect(session: Session, gen: Generation) -> None:
    """只在頁面對應欄位為空時寫入（一次條件式 UPDATE），絕不取代已選的；任何失敗只記 log。
    空欄位的頁面不可能選著合成圖，所以不需要重設審核狀態。"""
    try:
        slot = _SLOT.get(gen.kind)
        if (
            slot is None
            or gen.page_id is None
            or gen.status != GenStatus.SUCCEEDED
            or gen.discarded
            or not gen.image_path
            or (gen.kind == GenKind.POSE and not gen.mask_path)
        ):
            return
        field = getattr(Page, slot)
        session.exec(
            update(Page)
            .where(col(Page.id) == gen.page_id, field.is_(None))
            .values(**{slot: gen.id}, updated_at=utcnow())
        )
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        log.exception("自動選用失敗（generation %s）", getattr(gen, "id", None))
