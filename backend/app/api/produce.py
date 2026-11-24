"""「開始做這本書」（Phase 13 §4.1）與 B 整本書（§4.3）的端點。

- `GET /books/{id}/produce/plan`：要排幾張背景／角色動作、要幾次、估計秒數、今天剩幾次、放得下幾次。
  `auto_cleanup` 頁的背景算 2 次（背景＋自動清理）；total、fits、estimate_s 都是次數。
- `POST /books/{id}/produce`：依頁面順序、每頁先背景再角色動作，補排缺的素材。只預留今天放得下的
  （使用者決定：放得下多少做多少，一頁的兩張放不下也先排放得下的那張；auto_cleanup 頁的背景要 2 次都放得下
  才排，不會只排背景不排清理，放不下就從那裡開始留到明天），其餘頁面回 deferred_pages
  （「還有 N 頁明天繼續」）。已有排隊中／執行中的同種素材就略過，所以按兩次不會重複。
  設定頁改了場景／動作而標了「要重畫」的頁，也照缺的素材排：take（好了換上去，角色動作另標 review_pose），
  排到時清掉旗標，放不下的旗標留著。
  已經有背景與角色動作、還沒可以印（或「請看一下」：C 的「再試一次」）、也沒有排隊中／執行中流程的頁面
  排頁面流程（CPU，不計次數）。
  有素材要排卻一次都不剩 → 429「今天的生成次數已用完，明天再來」（同時有頁面流程可排時照排、回 202）。
  與「產生全部素材」共用 BATCH_LOCK、略過規則與任務組法（services/jobs.py）。
- `GET /books/{id}/overview`：每頁狀態（working＞attention＞ready＞todo）、做好幾頁（progress_pages）、本輪進度、
  還有幾頁沒排（不含「要重畫」的，它們另算 redraw_count）。review_pose（換了新的主角動作）與 review_frame
  （肖像頁的背景找不到框）是軟標記：算做好、繼續做不因它重排；每頁的 review 是「有軟標記」。

本輪進度：queued／running 是「現在」這本書排隊中／執行中的生成（任何種類）與頁面流程；done 是
book.produce_started_at 之後建立、已結束的生成與頁面流程。produce 有排到東西、而且當時這本書沒有進行中的
工作（或從沒按過）才把 produce_started_at 設成現在：工作還在跑時再按，算同一輪，做完的數字不歸零。
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, or_, select

from app.api.deps import current_owner, get_session, get_worker, owned_book
from app.api.pages import page_outs
from app.api.schemas import (
    BookOverviewOut,
    OverviewPageOut,
    ProduceOut,
    ProducePlanOut,
    ProgressPages,
    RunProgress,
)
from app.api.urls import generation_image_url, lineart_url_is_current
from app.models import Generation, GenKind, GenStatus, Owner, PageStatus, PageTask, PageTaskStatus, utcnow
from app.services import usage
from app.services.jobs import (
    BATCH_LOCK, BookState, add_queued, build_job, clear_redraw_flag, has_character, job_cost,
)
from app.services.page_state import GEN_ACTIVE, SOFT_ATTENTIONS, TASK_ACTIVE, hard_attention, page_state
from app.services.pipeline import enqueue_page_task
from app.services.slots import not_upload
from app.services.worker import GenerationWorker

router = APIRouter(prefix="/api", tags=["produce"])

SECONDS_PER_IMAGE = 22  # Qwen 4 步：每張約 22 秒（spec §4.1）


def _needed(session: Session, book, state: BookState, *, redraw: bool):
    """要排的 (頁面, 種類)，依頁面順序、每頁先背景再角色動作。redraw：連標了「要重畫」的也算（繼續做）。"""
    return [(p, k) for p, k, reason in state.plan(has_character(session, book), redraw=redraw) if reason is None]


def _tasks(session: Session, page_ids: list[int], started: datetime | None) -> list[PageTask]:
    """進行中的工作，加上這一輪（created_at >= started）開始後的；更早已完成的不載入（A16）。"""
    if not page_ids:
        return []
    recent = col(PageTask.status).in_(TASK_ACTIVE)
    if started is not None:
        recent = or_(recent, col(PageTask.created_at) >= started)
    return list(session.exec(select(PageTask).where(col(PageTask.page_id).in_(page_ids), recent)).all())


def _redraw_count(pages, has_char: bool) -> int:
    """標了「要重畫」而且畫得出來的頁：場景／動作不是空白（角色動作還要有角色）；同 BookState.skip_reason。"""
    return sum(
        (p.redraw_background and bool(p.scene_prompt.strip()))
        or (p.redraw_pose and bool(p.action_prompt.strip()) and has_char)
        for p in pages
    )


@router.get("/books/{book_id}/produce/plan", response_model=ProducePlanOut)
def produce_plan(
    book_id: int,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> ProducePlanOut:
    book = owned_book(session, owner, book_id)
    needed = _needed(session, book, BookState(session, book_id), redraw=True)
    backgrounds = sum(k == GenKind.BACKGROUND for _, k in needed)
    costs = [job_cost(p, k) for p, k in needed]  # auto_cleanup 頁的背景連清理算 2 次
    total = sum(costs)
    remaining = usage.remaining(session, owner, usage.clock())
    fits = sum(costs[:usage.fitting_prefix(costs, remaining)])  # 同 produce：放不下的那筆之後都留到明天
    return ProducePlanOut(
        backgrounds=backgrounds,
        poses=len(needed) - backgrounds,
        total=total,
        estimate_s=fits * SECONDS_PER_IMAGE,
        remaining=remaining,
        fits=fits,
    )


@router.post("/books/{book_id}/produce", status_code=202, response_model=ProduceOut)
def produce(
    book_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> ProduceOut:
    with BATCH_LOCK:  # 與「產生全部素材」互斥：同時按也不會重複排
        book = owned_book(session, owner, book_id)
        state = BookState(session, book_id)
        needed = _needed(session, book, state, redraw=True)
        page_ids = [p.id for p in state.pages]
        tasks = _tasks(session, page_ids, None)  # 只需要進行中的
        busy_pages = {t.page_id for t in tasks if t.status in TASK_ACTIVE}
        pipeline_pages = [
            p.id for p in state.pages
            # 筆畫還沒寫回、或「請看一下」（C 的「再試一次」）：再跑一次；軟標記 review_pose 不算
            if (p.status != PageStatus.PASSED or p.pending_user_strokes is not None or hard_attention(p.attention))
            and p.id not in busy_pages and state.has_both_images(p)
        ]
        if not needed and not pipeline_pages:
            return ProduceOut(queued=[], deferred_pages=[], pipeline_pages=[])
        in_progress = bool(busy_pages) or session.exec(
            select(Generation.id).where(Generation.book_id == book_id, col(Generation.status).in_(GEN_ACTIVE))
        ).first() is not None
        started = utcnow()  # 在建立這一輪的工作之前取，它們的 created_at 才會 ≥ 開始時間
        try:
            granted, extra = 0, {}
            if needed:
                try:
                    day, granted = usage.reserve_prefix(
                        session, owner, [job_cost(p, k) for p, k in needed], usage.clock())
                    extra = usage.usage_extra(owner, day)
                except HTTPException:
                    if not pipeline_pages:  # 什麼都做不了：429
                        raise
            gens = []
            for p, k in needed[:granted]:
                gens.append(add_queued(session, build_job(session, book, p, k, redraw=True), extra))
                clear_redraw_flag(session, p.id, k)  # 排到了：「要重畫」清掉（放不下的留著）
            for pid in pipeline_pages:
                enqueue_page_task(session, pid)
            if book.produce_started_at is None or not in_progress:
                book.produce_started_at = started
                session.add(book)
            session.commit()
        except Exception:
            session.rollback()
            raise
        worker.notify()
        deferred = list(dict.fromkeys(p.id for p, _ in needed[granted:]))
        return ProduceOut(queued=[g.id for g in gens], deferred_pages=deferred, pipeline_pages=pipeline_pages)


@router.get("/books/{book_id}/overview", response_model=BookOverviewOut)
def book_overview(
    book_id: int,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> BookOverviewOut:
    """只讀資料庫：不算線稿、不碰存放的物件。"""
    book = owned_book(session, owner, book_id)
    state = BookState(session, book_id)
    pages = state.pages
    started = book.produce_started_at

    queued = running = done = active_gens = 0
    busy: set[int] = set()
    for page_id, status, created_at in session.exec(
        select(Generation.page_id, Generation.status, Generation.created_at).where(
            Generation.book_id == book_id,
            # 只算排隊的工作：合成圖是頁面流程裡同步做的、上傳的背景不排隊
            col(Generation.kind) != GenKind.COMPOSITE,
            not_upload(),
        )
    ).all():
        if status in GEN_ACTIVE:
            active_gens += 1
            queued += status == GenStatus.QUEUED
            running += status == GenStatus.RUNNING
            if page_id is not None:
                busy.add(page_id)
        elif started is not None and created_at >= started:
            done += 1
    for t in _tasks(session, [p.id for p in pages], started):
        if t.status in TASK_ACTIVE:
            queued += t.status == PageTaskStatus.QUEUED.value
            running += t.status == PageTaskStatus.RUNNING.value
            busy.add(t.page_id)
        elif started is not None and t.created_at >= started:
            done += 1

    needs = {p.id for p, _ in _needed(session, book, state, redraw=False)}  # 「要重畫」的另外算（redraw_count）
    out = []
    for page, po in zip(pages, page_outs(session, pages)):
        st = page_state(page, busy)
        gap_count = None
        fresh = lineart_url_is_current(page.id, page.lineart_key, po.lineart_url)
        if fresh and page.qa_json:
            gap_count = json.loads(page.qa_json).get("gap_count")
        sel = page.selected_generation_id
        out.append(OverviewPageOut(
            id=page.id,
            position=page.position,
            state=st,
            gap_count=gap_count,
            caption_zh=page.caption_zh,
            caption_en=page.caption_en,
            caption_show_zh=page.caption_show_zh,
            caption_show_en=page.caption_show_en,
            thumb_url=generation_image_url(sel) if sel is not None else None,
            lineart_url=po.lineart_url,
            attention_detail=page.attention_detail if page.attention else None,
            review=page.attention in SOFT_ATTENTIONS,
        ))
    return BookOverviewOut(
        book_id=book_id,
        pages=out,
        progress=RunProgress(queued=queued, running=running, done=done),
        progress_pages=ProgressPages(ready=sum(p.state == "ready" for p in out), total=len(pages)),
        estimate_s=active_gens * SECONDS_PER_IMAGE,
        deferred_count=len(needs),
        redraw_count=_redraw_count(pages, has_character(session, book)),
        run_started_at=started,
    )
