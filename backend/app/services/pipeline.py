"""頁面流程（Phase 13「開始做這本書」）：worker 在生成之間執行，每頁

  1. 選圖：背景／角色動作是空的就用最新一張成功的生成（新圖產生好時 on_generation_succeeded 已換上，使用者的選擇優先）；
  2. 合成：用頁面存的位置，沒有就用預設位置（services/page_compose.compose_page，與確認合成共用）；
  3. 線稿；
  4. 自動補線：重算整層 auto 修線（覆寫，不附加；services/autofix.plan_autofix，與自動補線端點共用）；
  5. 可以印：PASSED、passed_at，清掉 attention（不需要角色檢查「對」）；軟標記 review_pose 保留。

失敗時頁面 attention="failed"、attention_detail 是中文說明，工作標成 FAILED。

「使用者選的」怎麼表示：頁面的 background／pose_generation_id 是目前選用的圖，auto_*_generation_id 是
頁面流程（或 worker 的自動選用）最後一次自己選的圖。欄位有值而且與 auto_* 不同 ＝ 使用者選的
（在舊編輯器點選、上傳背景、Phase 13 之前就選好的頁面），流程永遠不換掉它，auto_* 也不動；
欄位是空的，或等於 auto_* ＝ 自動選的，有更新的成功生成就換成最新的那張並同時記到 auto_*。
上傳的背景（engine="upload"）一律不當自動選圖的候選。

C 處理一頁的「換一張／AI 再畫一張」（request_json 的 take=true）：好了就換上去並記成 auto_*，連使用者之前選的
也換掉（使用者要的就是這張新圖），換之前存一份復原快照（services/history.take_generation）。
B 的批次重畫主角的 take 另帶 review=true：換上去的同一個交易把頁面標成 review_pose（第 5 步會保留它）。
重畫主角一次畫多張的第 2、3 張帶 candidate=true：只當候選，好了不選用、不排流程。
重新合成後，page.pending_user_strokes（C 的修改與復原／重做留下的使用者筆畫）寫進新合成圖的 user 層。
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from sqlalchemy import case, func, insert, literal
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, select, update

from app.models import (
    Book, Generation, GenKind, GenStatus, Page, PageStatus, PageTask, PageTaskKind, PageTaskStatus, StrokeLayer, utcnow,
)
from app.services import history
from app.services.engine import public_engine_error
from app.services.gen_request import CANDIDATE, REVIEW, TAKE
from app.services.slots import SLOTS, newest_usable_ai
from app.services.autofix import MAX_STROKES, MAX_TOTAL_POINTS, plan_autofix
from app.services.lineart_render import apply_strokes
from app.services.page_compose import (
    INPUTS_CHANGED, ComposeError, compose_page, is_usable, slot_change_values, usable_conditions,
)
from app.services.page_lineart import (
    LineartUnavailable, commit_layer, ensure_page_lineart, ink_before_user_layer, load_strokes, save_strokes,
    strokes_from_json, strokes_to_json,
)
from app.services.page_state import ATTENTION_REVIEW_POSE, REVIEW_POSE_DETAIL
from app.services.qa import evaluate
from app.storage import Storage

log = logging.getLogger(__name__)

PASSED = "passed"  # run_page_pipeline 的結果：這頁可以印了
WAITING = "waiting"  # 背景或角色動作還沒有：等之後的生成再跑

ATTENTION_FAILED = "failed"
MAX_ATTEMPTS = 3
PAGE_GONE = "找不到這一頁"
KEPT_CHANGING = "這一頁處理時一直被更動，請稍後再試"
UNEXPECTED = "自動處理這一頁時發生錯誤，請再試一次"
GENERATION_FAILED = "圖片產生失敗，請重新產生"
REQUEUE_DUPLICATE = "伺服器重新啟動；這一頁已有排隊中的工作"

_QUEUED = PageTaskStatus.QUEUED.value
_RUNNING = PageTaskStatus.RUNNING.value
_FINISHED = (PageTaskStatus.SUCCEEDED.value, PageTaskStatus.FAILED.value)
FINISHED_TASKS_KEPT = 5  # 每頁保留的已完成 page_task 筆數


class PipelineFailed(Exception):
    """頁面流程失敗。message 是給使用者看的中文（也寫進 attention_detail）；error 是記在工作上的細節。"""

    def __init__(self, message: str, error: str | None = None):
        super().__init__(message)
        self.message = message
        self.error = error or message


class _Stale(Exception):
    """執行中頁面被改了（另一個分頁、舊編輯器）：整個流程重跑。"""


class _PageGone(Exception):
    pass


class _Superseded(Exception):
    """一直被改而放棄，但這頁已有排隊中的流程：交給它，不設 attention。"""


def _has_queued_task(session: Session, page_id: int) -> bool:
    return session.exec(
        select(PageTask.id).where(col(PageTask.page_id) == page_id, col(PageTask.status) == _QUEUED)
    ).first() is not None


# ---- 喚醒 worker ----------------------------------------------------------------------

_wakeup: Callable[[], None] | None = None


def set_wakeup(fn: Callable[[], None] | None) -> None:
    """worker 啟動時登記自己的 notify（停止時清掉）：不經 API 的地方（例如線稿的 key 變了）排了流程後叫醒它。"""
    global _wakeup
    _wakeup = fn


def wake() -> None:
    """排了流程並提交之後呼叫；沒有 worker（測試、腳本）就什麼都不做。"""
    fn = _wakeup
    if fn is not None:
        try:
            fn()
        except Exception:  # noqa: BLE001  叫不醒只影響何時處理，工作已經在佇列
            log.warning("無法喚醒 worker", exc_info=True)


# ---- 排隊 ----------------------------------------------------------------------------


def enqueue_page_task(session: Session, page_id: int, kind: str = PageTaskKind.PIPELINE.value) -> bool:
    """排一個頁面流程；這頁已經有同種類排隊中（QUEUED）的工作就不再排。回傳有沒有新增。
    單一 INSERT ... SELECT ... WHERE NOT EXISTS，同時呼叫也不會重複。不 commit（呼叫端決定交易）。
    執行中（RUNNING）的工作不算：執行中又有改動，要在它之後再跑一次。"""
    kind = PageTaskKind(kind).value
    now = utcnow()
    ts = PageTask.__table__.c.created_at.type
    already = (
        select(PageTask.id)
        .where(col(PageTask.page_id) == page_id, col(PageTask.kind) == kind, col(PageTask.status) == _QUEUED)
        .exists()
    )
    res = session.exec(
        insert(PageTask).from_select(
            ["page_id", "kind", "status", "created_at", "updated_at"],
            select(literal(page_id), literal(kind), literal(_QUEUED), literal(now, ts), literal(now, ts))
            .where(~already),
        )
    )
    return res.rowcount == 1


def claim_page_task(session: Session) -> PageTask | None:
    """取最舊的 QUEUED 工作，以條件式 UPDATE 設為 RUNNING（被搶走就換下一個）；沒有回 None。"""
    while True:
        task_id = session.exec(
            select(PageTask.id).where(col(PageTask.status) == _QUEUED)
            .order_by(col(PageTask.created_at), col(PageTask.id))
        ).first()
        if task_id is None:
            return None
        res = session.exec(
            update(PageTask).where(col(PageTask.id) == task_id, col(PageTask.status) == _QUEUED)
            .values(status=_RUNNING, updated_at=utcnow())
        )
        session.commit()
        if res.rowcount == 1:
            return session.get(PageTask, task_id)


def finish_page_task(session: Session, task_id: int, status: PageTaskStatus, error: str | None = None) -> None:
    session.exec(
        update(PageTask).where(col(PageTask.id) == task_id, col(PageTask.status) == _RUNNING)
        .values(status=status.value, error=error, updated_at=utcnow())
    )
    page_id = session.exec(select(PageTask.page_id).where(col(PageTask.id) == task_id)).first()
    if page_id is not None:
        # 每頁只留最新 FINISHED_TASKS_KEPT 筆已完成的工作（A16）；但這一輪（created_at >= produce_started_at）
        # 的不刪，B 的「做好 N / M 張」要算它們
        stale = select(PageTask.id).where(
            col(PageTask.page_id) == page_id, col(PageTask.status).in_(_FINISHED)
        ).order_by(col(PageTask.id).desc()).offset(FINISHED_TASKS_KEPT)
        started = session.exec(
            select(Book.produce_started_at).join(Page, col(Page.book_id) == col(Book.id)).where(col(Page.id) == page_id)
        ).first()
        cond = col(PageTask.id).in_(stale)
        if started is not None:
            cond = cond & (col(PageTask.created_at) < started)
        session.exec(delete(PageTask).where(cond))
    session.commit()


def requeue_page_tasks(session: Session) -> int:
    """重啟時：上次執行到一半（RUNNING）的工作放回佇列（流程是確定性的、很便宜，重跑就好）。
    那頁已經有排隊中的工作時不重複排，執行中那筆標成 FAILED（不設 attention）。回傳放回佇列的數量。"""
    running = session.exec(
        select(PageTask).where(col(PageTask.status) == _RUNNING).order_by(col(PageTask.id))
    ).all()
    has_queued = set(session.exec(
        select(PageTask.page_id).where(col(PageTask.status) == _QUEUED)
    ).all())
    n = 0
    now = utcnow()
    for t in running:
        if t.page_id in has_queued:
            t.status, t.error = PageTaskStatus.FAILED.value, REQUEUE_DUPLICATE
        else:
            t.status, t.error = _QUEUED, None
            has_queued.add(t.page_id)
            n += 1
        t.updated_at = now
        session.add(t)
    session.commit()
    return n


# ---- worker 的生成結果 -------------------------------------------------------------------


def _flag(gen: Generation, key: str) -> bool:
    """request_json 的控制旗標是不是 true（壞掉的 JSON 當成沒有）。"""
    try:
        d = json.loads(gen.request_json or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(d, dict) and d.get(key) is True


def wants_take(gen: Generation) -> bool:
    return _flag(gen, TAKE)


def is_candidate(gen: Generation) -> bool:
    return _flag(gen, CANDIDATE)


def wants_review(gen: Generation) -> bool:
    return _flag(gen, REVIEW)


def on_generation_succeeded(session: Session, gen_id: int) -> bool:
    """頁面的背景／角色動作生成成功：馬上自動選用（舊編輯器不用等流程就看得到），並排這頁的流程。
    C 的「換一張」（take）直接換上這張（先存復原快照）；帶 review 的（批次重畫主角）換上去時同一個交易
    標成 review_pose。重畫主角的候選（candidate）什麼都不選、不排。回傳有沒有排（其他種類的生成不排）。"""
    gen = session.get(Generation, gen_id)
    if gen is None or gen.page_id is None or gen.kind not in SLOTS or is_candidate(gen):
        return False
    page_id = gen.page_id
    if wants_take(gen) and is_usable(gen, gen.kind, page_id):
        try:
            if history.take_generation(session, gen) and wants_review(gen):
                session.exec(
                    update(Page).where(col(Page.id) == page_id)
                    .values(attention=ATTENTION_REVIEW_POSE, attention_detail=REVIEW_POSE_DETAIL, updated_at=utcnow())
                )
            session.commit()
        except Exception:  # noqa: BLE001  換不上去（例如同時被改）：退回一般的自動選圖
            session.rollback()
            log.warning("換上新圖失敗（generation %s）", gen_id, exc_info=True)
            auto_select(session, page_id)  # take 的快照已經在 take_generation 裡（失敗時一起 rollback 掉了）
    else:
        auto_select(session, page_id, snapshot=True)
    enqueue_page_task(session, page_id)
    session.commit()
    return True


def on_generation_failed(session: Session, gen_id: int) -> None:
    """頁面的背景／角色動作生成失敗：頁面 attention="failed"，說明是生成的中文錯誤訊息。"""
    gen = session.get(Generation, gen_id)
    if gen is None or gen.page_id is None or gen.kind not in SLOTS or gen.status != GenStatus.FAILED:
        return
    session.exec(
        update(Page).where(col(Page.id) == gen.page_id)
        .values(attention=ATTENTION_FAILED, attention_detail=public_engine_error(gen.error, False) or GENERATION_FAILED, updated_at=utcnow())
    )
    session.commit()


# ---- 選圖 -----------------------------------------------------------------------------


def auto_select(session: Session, page_id: int, *, fill_only: bool = False, snapshot: bool = False) -> bool:
    """背景與角色動作：使用者選的不動；否則換成最新一張可用的生成（不含上傳的），並記到 auto_*。
    fill_only（頁面流程的第 1 步）：只補空的欄位，已經有圖的不換。換成新圖只在新圖產生好的時候
    （on_generation_succeeded）；所以「還原成自動畫的那張」與復原選回的圖，不會被流程換成更新的 AI 圖。
    換了圖而選定圖是合成圖時，清掉選定圖並退回草稿（同舊編輯器選圖）。條件式 UPDATE（以讀到的舊值為條件），
    與使用者同時選圖時使用者的選擇留下。snapshot：換掉的是原本就有的另一張圖、而且頁面有選定圖時，
    第一次換之前先存復原快照（同一個交易；一次呼叫最多一筆，什麼都沒換就一起 rollback），
    讓舊編輯器的「換一張」自動換圖可以在 C 按「↶ 復原」。commit；回傳有沒有換。"""
    page = session.get(Page, page_id)
    if page is None:
        return False
    session.refresh(page)
    changed = False
    snapshotted = False
    for kind, (field, auto_field) in SLOTS.items():
        current, auto = getattr(page, field), getattr(page, auto_field)
        if current is not None and (current != auto or fill_only):
            continue  # 使用者選的（或只補空的欄位）
        newest = newest_usable_ai(session, page_id, kind)
        if newest is None or newest == current:
            continue
        still_valid = (
            select(Generation.id)
            .where(col(Generation.id) == newest, col(Generation.page_id) == page_id, *usable_conditions(kind))
            .exists()
        )
        if snapshot and not fill_only and not snapshotted and current is not None and page.selected_generation_id is not None:
            history.push_snapshot(session, page)
            snapshotted = True
        res = session.exec(
            update(Page)
            .where(col(Page.id) == page_id,
                   col(getattr(Page, field)).is_not_distinct_from(current),
                   col(getattr(Page, auto_field)).is_not_distinct_from(auto),
                   still_valid)
            .values(**slot_change_values(field, newest), **{auto_field: newest})
        )
        changed |= res.rowcount == 1
    if snapshotted and not changed:
        session.rollback()  # 沒換成：快照也不留
    else:
        session.commit()
    return changed


# ---- 流程 -----------------------------------------------------------------------------


def run_page_pipeline(session: Session, storage: Storage, page_id: int) -> str:
    """跑一次頁面流程，回傳 PASSED 或 WAITING（背景或角色動作還沒有）。
    失敗時頁面 attention="failed"、attention_detail＝中文說明，丟 PipelineFailed（頁面已不存在時不設 attention）。
    頁面在執行中被改（輸入變了、選定圖換了）就從頭重跑，最多 MAX_ATTEMPTS 次；還是一直被改就失敗，但這頁已有
    排隊中的流程時不設 attention（下一個工作會用最新的內容再跑）。呼叫前不可有未提交的寫入。"""
    try:
        for _ in range(MAX_ATTEMPTS):
            try:
                return _attempt(session, storage, page_id)
            except _Stale:
                session.rollback()
        if _has_queued_task(session, page_id):
            raise _Superseded()
        raise PipelineFailed(KEPT_CHANGING)
    except _PageGone:
        session.rollback()
        raise PipelineFailed(PAGE_GONE) from None
    except _Superseded:  # 改動的那一方已經排了下一個流程：它會用最新的內容再跑，不設「請看一下」
        session.rollback()
        raise PipelineFailed(KEPT_CHANGING) from None
    except Exception as e:  # noqa: BLE001  任何失敗都要變成「請看一下」，不可卡在製作中
        session.rollback()
        if isinstance(e, PipelineFailed):
            failure = e
        elif isinstance(e, (ComposeError, LineartUnavailable)):
            failure = PipelineFailed(e.message)
        else:
            log.exception("頁面流程失敗（page %s）", page_id)
            failure = PipelineFailed(UNEXPECTED, f"{type(e).__name__}: {e}")
        _set_failed(session, page_id, failure.message)
        raise failure from e


def _set_failed(session: Session, page_id: int, message: str) -> None:
    try:
        session.exec(
            update(Page).where(col(Page.id) == page_id)
            .values(attention=ATTENTION_FAILED, attention_detail=message, updated_at=utcnow())
        )
        session.commit()
    except Exception:  # noqa: BLE001  工作仍會標成 FAILED
        session.rollback()
        log.exception("無法記下頁面 %s 的失敗", page_id)


def _write_pending(session: Session, page_id: int, gid: int, pending: str) -> None:
    """pending_user_strokes 寫進 gid 的 user 層並清掉欄位；條件式 UPDATE 確認頁面仍選定 gid、欄位沒被改，
    否則（被改了）整個流程重跑。內容跟現有的一樣就不寫（不為空筆畫多建一列）。"""
    strokes = strokes_from_json(pending)
    try:
        if strokes_to_json(strokes) != strokes_to_json(load_strokes(session, gid, layer=StrokeLayer.USER)):
            save_strokes(session, page_id, gid, strokes, layer=StrokeLayer.USER)
            session.flush()
        res = session.exec(
            update(Page)
            .where(col(Page.id) == page_id, col(Page.selected_generation_id) == gid,
                   col(Page.pending_user_strokes) == pending)
            .values(pending_user_strokes=None)
        )
        if res.rowcount != 1:
            raise _Stale()
        session.commit()
    except IntegrityError as e:  # 同時有請求建立同一層
        session.rollback()
        raise _Stale() from e
    except _Stale:
        session.rollback()
        raise


def _attempt(session: Session, storage: Storage, page_id: int) -> str:
    if session.get(Page, page_id) is None:
        raise _PageGone()
    # 1. 選圖（只補空的欄位；新圖產生好時 on_generation_succeeded 已經換上去）
    auto_select(session, page_id, fill_only=True)
    page = session.get(Page, page_id)
    if page is None:
        raise _PageGone()
    session.refresh(page)
    if page.background_generation_id is None or page.pose_generation_id is None:
        return WAITING
    # 2. 合成（沿用相同的既有合成圖）
    try:
        gid = compose_page(session, storage, page_id)
    except ComposeError as e:
        if e.message == INPUTS_CHANGED:
            raise _Stale() from e
        raise
    # 2b. C 的修改／復原留下的使用者筆畫：寫進新合成圖的 user 層
    session.refresh(page)
    if page.pending_user_strokes is not None:
        _write_pending(session, page_id, gid, page.pending_user_strokes)
        session.refresh(page)
    # 3–4. 線稿＋自動補線：以「底稿＋user 層」重算整層 auto（覆寫，不附加）
    width = page.line_width_px
    user = load_strokes(session, gid, layer=StrokeLayer.USER)
    pre = ink_before_user_layer(session, storage, page_id, gid, with_auto=False)
    qa = evaluate(apply_strokes(pre, user) if user else pre, width)
    auto = (plan_autofix(pre, user, qa.gap_count, qa.bridges, width,
                         room_strokes=MAX_STROKES, room_points=MAX_TOTAL_POINTS)
            if qa.bridges else [])
    if strokes_to_json(auto) != strokes_to_json(load_strokes(session, gid, layer=StrokeLayer.AUTO)):
        try:
            if not commit_layer(session, page_id, gid, auto, layer=StrokeLayer.AUTO):
                raise _Stale()
        except IntegrityError as e:  # 同時有請求建立同一層（例如舊編輯器的自動補線）
            raise _Stale() from e
    lin = ensure_page_lineart(session, storage, page_id, from_pipeline=True)
    # 5. 可以印（已經是 PASSED 的保留原本的通過時間）；清掉 failed 等，保留軟標記 review_pose
    now = utcnow()
    already = col(Page.status) == PageStatus.PASSED
    review = col(Page.attention) == ATTENTION_REVIEW_POSE
    res = session.exec(
        update(Page)
        .where(col(Page.id) == page_id, col(Page.selected_generation_id) == gid,
               col(Page.lineart_key) == lin.key)
        .values(status=PageStatus.PASSED,
                passed_at=func.coalesce(case((already, col(Page.passed_at))),
                                        literal(now, Page.__table__.c.passed_at.type)),
                forced_pass_reason=None,
                attention=case((review, ATTENTION_REVIEW_POSE), else_=None),
                attention_detail=case((review, col(Page.attention_detail)), else_=None),
                updated_at=now)
    )
    session.commit()
    if res.rowcount == 0:
        raise _Stale()
    return PASSED
