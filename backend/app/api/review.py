"""角色檢查與頁面通過／取消通過。一本書由一個人做 QA。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StringConstraints
from sqlalchemy import case, literal
from sqlmodel import Session, col, update

from app.api.deps import get_session, get_storage
from app.api.pages import _page_or_404, page_out
from app.api.schemas import PageOut
from app.models import CharacterCheck, Page, PageStatus, utcnow
from app.services.page_lineart import LineartUnavailable, ensure_page_lineart
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["review"])

NoteStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
ReasonStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]

NO_SELECTION = "這一頁還沒有選定圖片"
CHARACTER_NOT_CONFIRMED = "請先確認角色外型與設定一致"
CHANGED = "頁面剛被修改，請重新檢查"
STALE_CHECK = "選定的圖片已更換，請重新檢查"


class CharacterCheckIn(BaseModel):
    generation_id: int
    result: CharacterCheck
    note: NoteStr = ""


class PassIn(BaseModel):
    force_reason: ReasonStr | None = None


@router.post("/pages/{page_id}/character-check", response_model=PageOut)
def character_check(
    page_id: int, body: CharacterCheckIn, session: Session = Depends(get_session)
) -> PageOut:
    page = _page_or_404(session, page_id)
    values: dict = {"character_check": body.result, "qa_note": body.note, "updated_at": utcnow()}
    if body.result == CharacterCheck.INCONSISTENT:
        passed = col(Page.status) == PageStatus.PASSED
        values["status"] = case((passed, literal(PageStatus.DRAFT, Page.__table__.c.status.type)), else_=col(Page.status))
        values["passed_at"] = case((passed, None), else_=col(Page.passed_at))
    res = session.exec(
        update(Page)
        .where(col(Page.id) == page_id, col(Page.selected_generation_id) == body.generation_id)
        .values(**values)
    )
    session.commit()
    if not res.rowcount:
        raise HTTPException(409, STALE_CHECK)
    session.refresh(page)
    return page_out(session, page)


@router.post("/pages/{page_id}/pass", response_model=PageOut)
def pass_page(
    page_id: int,
    body: PassIn,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> PageOut:
    page = _page_or_404(session, page_id)
    gid = page.selected_generation_id
    if gid is None:
        raise HTTPException(409, NO_SELECTION)
    if page.character_check != CharacterCheck.CONSISTENT:
        raise HTTPException(409, CHARACTER_NOT_CONFIRMED)
    try:
        lin = ensure_page_lineart(session, storage, page_id)
    except LineartUnavailable as e:
        raise HTTPException(409, e.message) from e
    reason = (body.force_reason or "").strip()
    errors = lin.qa.errors
    if errors and not reason:
        reason = f"使用者確認完成（{lin.qa.gap_count} 個缺口）"
    now = utcnow()
    res = session.exec(
        update(Page)
        .where(
            col(Page.id) == page_id,
            col(Page.selected_generation_id) == gid,
            col(Page.lineart_key) == lin.key,
            col(Page.character_check) == CharacterCheck.CONSISTENT,
        )
        .values(
            status=PageStatus.PASSED,
            passed_at=now,
            forced_pass_reason=reason if errors else None,
            updated_at=now,
        )
    )
    session.commit()
    if res.rowcount == 0:
        raise HTTPException(409, CHANGED)
    session.refresh(page)
    return page_out(session, page)


@router.post("/pages/{page_id}/unpass", response_model=PageOut)
def unpass_page(page_id: int, session: Session = Depends(get_session)) -> PageOut:
    page = _page_or_404(session, page_id)
    page.status = PageStatus.DRAFT
    page.passed_at = None
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    session.refresh(page)
    return page_out(session, page)
