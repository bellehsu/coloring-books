"""C 處理一頁的復原／重做（Phase 13 §4.4）與「還原成自動畫的那張」。

快照（page_history.snapshot_json）：背景、角色動作、位置、動作文字、圖下方的字與要印哪幾行，
以及使用者筆畫（user 層）本身。Phase 13b 之前的快照沒有「要印哪幾行」：套用時當成兩行都印。

**紀錄與游標。** 每頁的快照依 seq 排成一列，page.history_cursor 是「目前狀態」對應的 seq：
- None＝在最新狀態（目前狀態還沒存成快照），沒有可重做的；
- C 的每個修改（push_snapshot）：刪掉 seq ≥ cursor 的（截掉重做），把目前狀態存到最後，cursor＝None；
- 復原：cursor 是 None 時先把目前狀態存到最後（重做才回得來），否則覆寫 cursor 那筆；套用 cursor 前一筆；
- 重做：覆寫 cursor 那筆，套用下一筆；
- 每頁最多 MAX_SNAPSHOTS 筆，最舊的丟掉。

**重新合成與筆畫。** 使用者筆畫跟著合成圖（generation）存，合成圖要等頁面流程才會有。所以會重新合成的修改
（換背景／角色動作、拖位置、還原圖、套用圖或位置不同的快照）把「新合成圖要用的 user 層」記在
page.pending_user_strokes：一般修改是 "[]"（重新合成清掉筆畫，不跳對話框；快照裡留著），復原／重做是快照裡的
筆畫。頁面流程合成之後把它寫進新合成圖的 user 層、清掉欄位（services/pipeline.py），再重算 auto 層。
等著寫回時，「目前的使用者筆畫」就是 pending_user_strokes（快照也存它）。

**快照裡的圖已不能用**（被捨棄、檔案被清）：保留目前的圖，其餘照套（文字、位置、筆畫）。
**auto_* 指到不能用的圖**：「還原成自動畫的那張」改用最新一張能用的 AI 圖（不含上傳的），並記成新的 auto_*；
一張都沒有就 409（中文訊息）。

所有函式都不 commit（呼叫端一起提交，失敗就整個 rollback）。
"""

from __future__ import annotations

import json

from sqlalchemy import func, insert, literal
from sqlmodel import Session, col, delete, select, update

from app.models import Generation, GenKind, Page, PageHistory, PageStatus, StrokeLayer, utcnow
from app.services.page_compose import is_usable, slot_change_values, usable_conditions
from app.services.slots import SLOTS, newest_usable_ai
from app.services.page_lineart import load_strokes, on_pipeline, save_strokes, strokes_from_json, strokes_to_json

MAX_SNAPSHOTS = 50

NOTHING_TO_UNDO = "沒有可以復原的步驟"
NOTHING_TO_REDO = "沒有可以重做的步驟"
HISTORY_CHANGED = "這一頁剛被另一個畫面更動，請重新整理後再試"
RECOMPOSE_PENDING = "這一頁正在重新合成，請稍候再畫"
RECOMPOSE_FAILED = "這一頁合成失敗，請先按「再試一次」"
NO_AUTO = {GenKind.BACKGROUND: "沒有自動畫的背景可以還原", GenKind.POSE: "沒有自動畫的角色動作可以還原"}

CLEARED = "[]"


class HistoryError(Exception):
    """復原／重做／還原做不到（中文訊息）；API 回 409。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---- 目前狀態 ---------------------------------------------------------------------------


def user_strokes_json(session: Session, page: Page) -> str:
    """目前的使用者筆畫（正規化的 JSON）：等著寫回新合成圖的優先，否則是選定圖的 user 層。"""
    if page.pending_user_strokes is not None:
        return strokes_to_json(strokes_from_json(page.pending_user_strokes))
    if page.selected_generation_id is None:
        return CLEARED
    return strokes_to_json(load_strokes(session, page.selected_generation_id, layer=StrokeLayer.USER))


def _placement(page: Page):
    return json.loads(page.placement_json) if page.placement_json else None


def snapshot_of(session: Session, page: Page) -> dict:
    return {
        "background_generation_id": page.background_generation_id,
        "pose_generation_id": page.pose_generation_id,
        "placement": _placement(page),
        "action_prompt": page.action_prompt,
        "caption_zh": page.caption_zh,
        "caption_en": page.caption_en,
        "caption_show_zh": page.caption_show_zh,
        "caption_show_en": page.caption_show_en,
        "user_strokes": json.loads(user_strokes_json(session, page)),
    }


def _dumps(snap: dict) -> str:
    return json.dumps(snap, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---- 紀錄 -----------------------------------------------------------------------------


def _append(session: Session, page_id: int, snap: dict) -> None:
    """存到最後（seq＝目前最大＋1，單一 INSERT ... SELECT，不會撞號）。"""
    ts = PageHistory.__table__.c.created_at.type
    next_seq = (
        select(func.coalesce(func.max(PageHistory.seq), 0) + 1)
        .where(col(PageHistory.page_id) == page_id)
        .scalar_subquery()
    )
    session.exec(insert(PageHistory).from_select(
        ["page_id", "seq", "snapshot_json", "created_at"],
        select(literal(page_id), next_seq, literal(_dumps(snap)), literal(utcnow(), ts)),
    ))


def _trim(session: Session, page_id: int, keep: set[int] = frozenset()) -> None:
    """只留最新的 MAX_SNAPSHOTS 筆（keep 裡的 seq 不丟）。"""
    newest = (
        select(PageHistory.id).where(col(PageHistory.page_id) == page_id)
        .order_by(col(PageHistory.seq).desc()).limit(MAX_SNAPSHOTS)
    )
    session.exec(delete(PageHistory).where(
        col(PageHistory.page_id) == page_id, col(PageHistory.id).not_in(newest), col(PageHistory.seq).not_in(keep),
    ))


def _overwrite(session: Session, page_id: int, seq: int, snap: dict) -> None:
    session.exec(update(PageHistory).where(col(PageHistory.page_id) == page_id, col(PageHistory.seq) == seq)
                 .values(snapshot_json=_dumps(snap)))


def push_snapshot(session: Session, page: Page) -> None:
    """C 的修改之前呼叫（同一個交易、不 commit）：截掉重做的部分，把目前狀態存到最後。
    page 要是最新的（呼叫端先 refresh）。"""
    cursor_now = select(Page.history_cursor).where(col(Page.id) == page.id).scalar_subquery()
    session.exec(delete(PageHistory).where(col(PageHistory.page_id) == page.id, col(PageHistory.seq) >= cursor_now))
    _append(session, page.id, snapshot_of(session, page))
    session.exec(update(Page).where(col(Page.id) == page.id).values(history_cursor=None))
    _trim(session, page.id)


def _seqs(session: Session, page_id: int) -> list[int]:
    return list(session.exec(
        select(PageHistory.seq).where(col(PageHistory.page_id) == page_id).order_by(col(PageHistory.seq))
    ).all())


def history_flags(session: Session, pages: list[Page]) -> dict[int, tuple[bool, bool]]:
    """多頁一次查：{page_id: (can_undo, can_redo)}。"""
    ids = [p.id for p in pages]
    if not ids:
        return {}
    bounds = {pid: (lo, hi) for pid, lo, hi in session.exec(
        select(PageHistory.page_id, func.min(PageHistory.seq), func.max(PageHistory.seq))
        .where(col(PageHistory.page_id).in_(ids)).group_by(PageHistory.page_id)
    ).all()}
    out = {}
    for p in pages:
        lo, hi = bounds.get(p.id, (None, None))
        if lo is None:
            out[p.id] = (False, False)
        elif p.history_cursor is None:
            out[p.id] = (True, False)
        else:
            out[p.id] = (lo < p.history_cursor, hi > p.history_cursor)
    return out


def _move_cursor(session: Session, page_id: int, old: int | None, new: int) -> None:
    """條件式 UPDATE（以讀到的游標為條件）：同時兩個復原／重做，後到的 409。也讓這個交易先拿到寫入鎖。"""
    res = session.exec(update(Page).where(col(Page.id) == page_id,
                                          col(Page.history_cursor).is_not_distinct_from(old))
                       .values(history_cursor=new, updated_at=utcnow()))
    if res.rowcount != 1:
        raise HistoryError(HISTORY_CHANGED)


def _snapshot_at(session: Session, page_id: int, seq: int) -> dict:
    row = session.exec(select(PageHistory.snapshot_json).where(
        col(PageHistory.page_id) == page_id, col(PageHistory.seq) == seq)).one()
    return json.loads(row)


def undo(session: Session, page: Page) -> bool:
    """復原一步；回傳要不要排頁面流程。沒有可復原的丟 HistoryError。"""
    seqs, cursor = _seqs(session, page.id), page.history_cursor
    earlier = [s for s in seqs if cursor is None or s < cursor]
    if not earlier:
        raise HistoryError(NOTHING_TO_UNDO)
    target = earlier[-1]
    current = snapshot_of(session, page)
    _move_cursor(session, page.id, cursor, target)
    if cursor is None:
        _append(session, page.id, current)  # 目前狀態存到最後：重做才回得來
        _trim(session, page.id, keep={target})
    else:
        _overwrite(session, page.id, cursor, current)
    return apply_snapshot(session, page, _snapshot_at(session, page.id, target))


def redo(session: Session, page: Page) -> bool:
    """重做一步；回傳要不要排頁面流程。沒有可重做的丟 HistoryError。"""
    cursor = page.history_cursor
    later = [s for s in _seqs(session, page.id) if cursor is not None and s > cursor]
    if not later:
        raise HistoryError(NOTHING_TO_REDO)
    target = later[0]
    current = snapshot_of(session, page)
    _move_cursor(session, page.id, cursor, target)
    _overwrite(session, page.id, cursor, current)
    return apply_snapshot(session, page, _snapshot_at(session, page.id, target))


# ---- 套用 ------------------------------------------------------------------------------


def set_slot(session: Session, page_id: int, kind: GenKind, gen_id: int, *, auto: bool = False,
             new_selection: bool = True) -> bool:
    """把背景／角色動作換成 gen_id（同 _pick：選定圖是合成圖就清掉、退回草稿），auto=True 時也記成 auto_*。
    new_selection 見 slot_change_values（復原／重做傳 False：「要重畫」留著）。
    條件式 UPDATE 再確認圖仍可用；回傳有沒有寫入。"""
    field, auto_field = SLOTS[kind]
    still_valid = (
        select(Generation.id)
        .where(col(Generation.id) == gen_id, col(Generation.page_id) == page_id, *usable_conditions(kind))
        .exists()
    )
    values = slot_change_values(field, gen_id, new_selection=new_selection)
    if auto:
        values[auto_field] = gen_id
    res = session.exec(update(Page).where(col(Page.id) == page_id, still_valid).values(**values))
    return res.rowcount == 1


def set_pending_strokes(session: Session, page_id: int, strokes_json: str | None) -> None:
    session.exec(update(Page).where(col(Page.id) == page_id)
                 .values(pending_user_strokes=strokes_json, updated_at=utcnow()))


def apply_snapshot(session: Session, page: Page, snap: dict) -> bool:
    """套用快照：選回圖與位置、文字，以及使用者筆畫。圖或位置不同（或已經在等重新合成）時，筆畫記在
    pending_user_strokes 等流程合成後寫回；否則直接寫回選定圖的 user 層。回傳要不要排頁面流程。"""
    pid = page.id
    recompose = False
    for kind, (field, _auto) in SLOTS.items():
        want = snap.get(field)
        if want is None or want == getattr(page, field):
            continue
        if not is_usable(session.get(Generation, want), kind, pid):
            continue  # 圖已不能用（捨棄、清掉）：保留目前的
        if set_slot(session, pid, kind, want, new_selection=False):  # 選回舊的圖：「要重畫」留著
            recompose = True
    placement = snap.get("placement")
    if placement != _placement(page):
        recompose = True
    session.exec(update(Page).where(col(Page.id) == pid).values(
        placement_json=None if placement is None else json.dumps(placement),
        action_prompt=snap.get("action_prompt") or "",
        caption_zh=snap.get("caption_zh"),
        caption_en=snap.get("caption_en"),
        caption_show_zh=bool(snap.get("caption_show_zh", True)),
        caption_show_en=bool(snap.get("caption_show_en", True)),
        updated_at=utcnow(),
    ))
    want_strokes = strokes_to_json(strokes_from_json(json.dumps(snap.get("user_strokes") or [])))
    if recompose or page.pending_user_strokes is not None:
        set_pending_strokes(session, pid, want_strokes)
        return True
    gid = page.selected_generation_id
    if gid is None or want_strokes == user_strokes_json(session, page):
        return False
    _write_user_layer(session, page, strokes_from_json(want_strokes))
    return True


def _write_user_layer(session: Session, page: Page, strokes) -> None:
    """直接改選定圖的 user 層（不用重新合成時）。頁面流程做的頁由呼叫端排流程重跑；
    流程處理不了的頁（舊流程的整頁圖）照舊編輯器的規則退回草稿。"""
    gid = page.selected_generation_id
    save_strokes(session, page.id, gid, strokes, layer=StrokeLayer.USER)
    if not on_pipeline(page, session.get(Generation, gid)):
        session.exec(update(Page).where(col(Page.id) == page.id)
                     .values(status=PageStatus.DRAFT, passed_at=None, updated_at=utcnow()))


# ---- 還原成自動畫的那張 ------------------------------------------------------------------


def auto_target(session: Session, page: Page, kind: GenKind) -> int:
    """「自動畫的那張」：auto_* 還能用就是它；否則最新一張能用的 AI 圖（不含上傳的）。都沒有丟 HistoryError。"""
    _field, auto_field = SLOTS[kind]
    auto = getattr(page, auto_field)
    if auto is not None and is_usable(session.get(Generation, auto), kind, page.id):
        return auto
    newest = newest_usable_ai(session, page.id, kind)
    if newest is None:
        raise HistoryError(NO_AUTO[kind])
    return newest


def restore(session: Session, page: Page, item: str) -> bool:
    """「還原成自動畫的那張」：背景／角色動作換回 auto_*（並清掉筆畫，同重新合成）；線條清掉使用者筆畫。
    有改動時先存快照；回傳有沒有改（要不要排頁面流程）。"""
    if item == "lines":
        if user_strokes_json(session, page) == CLEARED:
            return False
        push_snapshot(session, page)
        if page.pending_user_strokes is not None:
            set_pending_strokes(session, page.id, CLEARED)
        else:
            _write_user_layer(session, page, [])
        return True
    kind = GenKind.BACKGROUND if item == "background" else GenKind.POSE
    field, auto_field = SLOTS[kind]
    target = auto_target(session, page, kind)
    if getattr(page, field) == target:  # 已經是那張（auto_* 是舊的、不能用了：順手記成新的）
        if getattr(page, auto_field) != target:
            session.exec(update(Page).where(col(Page.id) == page.id).values(**{auto_field: target}))
        return False
    push_snapshot(session, page)
    set_pending_strokes(session, page.id, CLEARED)
    if not set_slot(session, page.id, kind, target, auto=True):
        raise HistoryError(NO_AUTO[kind])
    return True


def has_both_slots(page: Page) -> bool:
    """背景與角色動作都選好了：頁面流程才合成得出來（等著重新合成才有意義）。"""
    return all(getattr(page, field) is not None for field, _auto in SLOTS.values())


def can_compose_after(page: Page, kind: GenKind) -> bool:
    """把 kind 那一格換成新圖之後，背景與角色動作是不是都有（另一格已經選好）。"""
    return all(getattr(page, field) is not None for k, (field, _auto) in SLOTS.items() if k != kind)


def take_generation(session: Session, gen: Generation) -> bool:
    """C 的「換一張／AI 再畫一張」產生好了：先存快照，換上這張並記成 auto_*（使用者之前選的也換掉：
    這就是他要的新圖），筆畫清掉（重新合成）。不 commit；回傳有沒有換，換不上去丟 HistoryError。"""
    page = session.get(Page, gen.page_id)
    if page is None:
        return False
    session.refresh(page)
    field, _auto = SLOTS[gen.kind]
    if getattr(page, field) == gen.id:
        return False
    push_snapshot(session, page)
    if can_compose_after(page, gen.kind):  # 少了另一張就合成不了：標成等著重新合成會讓 C 一直顯示「製作中」
        set_pending_strokes(session, page.id, CLEARED)
    if not set_slot(session, page.id, gen.kind, gen.id, auto=True):
        raise HistoryError("新的圖已不能用")  # 呼叫端 rollback（快照也不留）
    return True
