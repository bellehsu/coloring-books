"""生成任務的組裝與排隊：單張、批次（產生全部素材、重畫主角）、「開始做這本書」、再試一次共用。

路由模組（api/compose、produce、history、books）只負責 HTTP；任務怎麼組、整批怎麼預留額度與
排進佇列、批次怎麼判斷要略過哪些頁，都在這裡。
"""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from sqlmodel import Session, col, select, update

from app.models import Book, Generation, GenKind, GenStatus, Owner, Page
from app.services import usage
from app.services.gen_request import (
    AUTO_CLEANUP, CANDIDATE, CLEANUP, CLEANUP_RESERVED, REVIEW, TAKE, encode_request, is_candidate, wants_review,
    wants_take,
)
from app.services.layout import compute_layout
from app.services.no_character import no_character_scene
from app.services.page_compose import is_usable
from app.services.prompts import (
    GENERIC_CLEANUP_PROMPT,
    POSE_SIZE,
    has_facing,
    pick_background_variation,
    pick_pose_variation,
    qwen_background_prompt,
    qwen_pose_prompt,
)
from app.services.slots import UPLOAD_ENGINE

if TYPE_CHECKING:  # 只用在型別：worker → pipeline → jobs，執行時匯入會循環
    from app.services.worker import GenerationWorker

MAX_SEED = 2**32 - 1

# 批次背景：後端是單一行程、handler 是同步的，用一把鎖序列化，兩個分頁同時按也不會重複排隊
# （compose 的批次、produce、history 的「再試一次」共用這一把）
BATCH_LOCK = threading.Lock()


class Job:
    """還沒加進 session 的排隊任務：generation 加上組 request_json 用的參數。"""

    def __init__(self, gen: Generation, *, character_image: str | None = None,
                 variation: str | None = None, extra: dict | None = None) -> None:
        self.gen = gen
        self.character_image = character_image
        self.variation = variation
        self.extra = extra or {}

    @property
    def cost(self) -> int:
        """預留幾次當日額度：auto_cleanup 頁的背景連它的自動清理算 2 次。"""
        return 2 if self.extra.get(CLEANUP_RESERVED) else 1


NO_CHARACTER_SKIP = "這頁不放主角"  # 批次／「開始做這本書」略過不放主角頁的角色動作時的原因


def job_cost(page: Page, kind: GenKind) -> int:
    """這頁這種素材排一筆要幾次（同 Job.cost；「開始做這本書」先算次數再組任務）。"""
    return 2 if kind == GenKind.BACKGROUND and page.auto_cleanup else 1


def add_queued(session: Session, job: Job, usage_extra: dict) -> Generation:
    """把排隊任務加進目前的交易（不 commit）；request_json 由 gen 的欄位加上參數與計數紀錄組成。"""
    gen = job.gen
    session.add(gen)
    session.flush()
    gen.request_json = encode_request(
        kind=gen.kind,
        prompt=gen.prompt,
        negative_prompt=gen.negative_prompt,
        seed=gen.seed,
        width=gen.width,
        height=gen.height,
        character_image=job.character_image,
        variation=job.variation,
        extra={**job.extra, **usage_extra},
    )
    return gen


def enqueue_jobs(
    session: Session, worker: GenerationWorker, owner: Owner, jobs: list[Job]
) -> list[Generation]:
    """同一個交易：預留這些任務的當日額度（每筆 Job.cost 次；不夠就 429，什麼都不排），再新增全部任務後
    一起 commit。沒有任務就不計數。"""
    if not jobs:
        return []
    try:
        day = usage.reserve(session, owner, sum(job.cost for job in jobs), usage.clock())
        extra = usage.usage_extra(owner, day)
        gens = [add_queued(session, job, extra) for job in jobs]
        session.commit()
    except Exception:
        session.rollback()
        raise
    worker.notify()
    return gens


def last_variations(
    session: Session, *where, n: int = 1, match: Callable[[dict], bool] | None = None
) -> list[str | None]:
    """符合 where 的生成（依 id 由新到舊，不論狀態、是否丟棄）最近 n 筆的構圖／動作變化語；request_json
    壞掉的略過。預設只收有變化語（字串）的列；match 可改成別的列條件（收到的列取它的 variation，可能是 None）。
    再產生時排除它們，連續幾張的構圖才會不同。"""
    out: list[str | None] = []
    for rj in session.exec(
        select(Generation.request_json).where(*where).order_by(col(Generation.id).desc())
    ):
        try:
            d = json.loads(rj)
        except (TypeError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if match is None:
            if not isinstance(d.get("variation"), str):
                continue
        elif not match(d):
            continue
        v = d.get("variation")
        out.append(v if isinstance(v, str) else None)
        if len(out) >= n:
            break
    return out


def _last_simple_variation(session: Session, page_id: int) -> str | None:
    """這頁最近一張簡潔背景（不論有沒有變化語）的變化語；沒有回 None。"""
    found = last_variations(
        session, col(Generation.page_id) == page_id, col(Generation.kind) == GenKind.BACKGROUND,
        match=lambda d: d.get("background_style") == "simple",
    )
    return found[0] if found else None


def background_job(
    session: Session, book: Book, page: Page, *, include_character: bool | None = None
) -> Job:
    """單張與批次共用：用頁面場景描述排一筆簡潔（Qwen）背景，負向空字串，不用草圖。
    構圖變化語排除這頁上一張簡潔背景用的。auto_cleanup 頁帶 CLEANUP_RESERVED：連自動清理預留 2 次。
    不放主角的頁（include_character 為 None 時看頁面的值；切換端點用 False/True 覆寫畫「切換後」的背景）：
    prompt 用改寫成「畫面中間、畫很大」的場景、變化語只挑「在中間」類；user_text 仍是原文。"""
    width, height = compute_layout(book.paper_size, book.print_mode).generation_size
    with_char = page.include_character if include_character is None else include_character
    exclude = _last_simple_variation(session, page.id)
    scene = page.scene_prompt
    if with_char:
        variation = pick_background_variation(random.Random(), exclude=exclude)
    else:
        variation = pick_background_variation(random.Random(), exclude=exclude, center=True)
        scene = no_character_scene(scene)
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.BACKGROUND,
        user_text=page.scene_prompt,
        prompt=qwen_background_prompt(scene, variation),
        negative_prompt="",
        seed=random.randint(0, MAX_SEED),
        width=width,
        height=height,
        request_json="",
    )
    extra: dict = {"background_style": "simple"}
    if page.auto_cleanup:
        extra[CLEANUP_RESERVED] = True
    return Job(gen, variation=variation, extra=extra)


def cleanup_job(session: Session, book: Book, page: Page, source: Generation) -> Job:
    """背景清理（「清掉框外雜物」）：用 qwen_edit_cleanup 編輯 source（這頁的背景），尺寸與它相同。
    指令＝頁面的 cleanup_prompt，沒有就用 GENERIC。request_json 帶 cleanup 旗標（來源列被刪掉後仍認得出是清理）。"""
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.BACKGROUND,
        user_text="",
        prompt=page.cleanup_prompt or GENERIC_CLEANUP_PROMPT,
        negative_prompt="",
        seed=random.randint(0, MAX_SEED),
        width=source.width,
        height=source.height,
        request_json="",
        source_generation_id=source.id,
    )
    return Job(gen, extra={CLEANUP: True})


def auto_cleanup_job(session: Session, book: Book, page: Page, source: Generation) -> Job:
    """auto_cleanup 頁的 AI 背景好了：清理它的任務（帶 AUTO_CLEANUP）。take／review 旗標照抄來源
    （一鍵的「AI 再畫一張」好了一定換上去，換上去的是清好的這張）；user_text 也照抄來源（清好的仍是那段
    場景文字的圖：換上去時「要重畫」的文字保護照舊）。"""
    job = cleanup_job(session, book, page, source)
    job.gen.user_text = source.user_text
    job.extra[AUTO_CLEANUP] = True
    if wants_take(source):
        job.extra[TAKE] = True
    if wants_review(source):
        job.extra[REVIEW] = True
    return job


def pose_job(session: Session, book: Book, page: Page, action: str, *, picked: Sequence[str | None] = ()) -> Job:
    """角色動作（Qwen）：以角色參考圖編輯出新動作；呼叫端負責確認有角色（has_character）。
    picked：同一次呼叫裡已經排好（還沒寫進資料庫）的變化語，由舊到新；跟這頁最近的一起排除。"""
    char = session.get(Generation, book.character_generation_id)
    variation = pick_pose_variation(
        random.Random(),
        exclude=[*reversed(picked), *last_variations(
            session, col(Generation.page_id) == page.id, col(Generation.kind) == GenKind.POSE, n=2
        )],
        facing=has_facing(action),
    )
    width, height = POSE_SIZE
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.POSE,
        user_text=action,
        prompt=qwen_pose_prompt(action, variation),
        negative_prompt="",
        seed=random.randint(0, MAX_SEED),
        width=width,
        height=height,
        request_json="",
    )
    return Job(gen, character_image=char.image_path, variation=variation, extra={"pose_mode": "precise"})


def take(job: Job, oneclick: bool) -> Job:
    """oneclick（C 的「換一張／AI 再畫一張」）：好了一定換上去。"""
    if oneclick:
        job.extra[TAKE] = True
    return job


def redraw_jobs(
    session: Session, book: Book, page: Page, action: str, count: int, *, take_first: bool, review: bool
) -> list[Job]:
    """重畫主角：這頁 count 張角色動作。第 1 張 take_first 時帶 TAKE（好了自動換上去）、review 時帶 REVIEW
    （換上去時標成 review_pose）；第 2 張起帶 CANDIDATE（只當候選）。每張都加進排除清單：沒有面向語時有 4 個
    變化語、排除最近兩個，3 張彼此不同；動作有面向語時只有 2 個、只排除上一個，相鄰兩張不同，第 3 張會跟第 1 張一樣。"""
    jobs: list[Job] = []
    for i in range(count):
        job = pose_job(session, book, page, action, picked=[j.variation for j in jobs])
        if i == 0:
            take(job, take_first)
            if review:
                job.extra[REVIEW] = True
        else:
            job.extra[CANDIDATE] = True
        jobs.append(job)
    return jobs


def has_character(session: Session, book: Book) -> bool:
    """繪本已設定角色且角色圖還在（角色動作要用它當參考圖）。"""
    char = (
        session.get(Generation, book.character_generation_id)
        if book.character_generation_id is not None
        else None
    )
    return char is not None and bool(char.image_path)


def build_job(session: Session, book: Book, page: Page, kind: GenKind, *, redraw: bool = False) -> Job:
    """批次與「開始做這本書」共用：這頁這種素材的排隊任務（呼叫端確認不用略過）。
    redraw（「繼續做」）且這頁這種素材標了「要重畫」：take（好了換上去、有復原快照）；角色動作另帶 REVIEW
    （換上去時標成 review_pose），背景不帶。"""
    if kind == GenKind.BACKGROUND:
        job = background_job(session, book, page)
    else:
        job = pose_job(session, book, page, page.action_prompt)
    if redraw and redraw_flag(page, kind):
        take(job, True)
        if kind == GenKind.POSE:
            job.extra[REVIEW] = True
    return job


def redraw_flag(page: Page, kind: GenKind) -> bool:
    """設定頁改了場景／動作，這頁這種素材等「繼續做」重畫（「要重畫」）。"""
    return page.redraw_background if kind == GenKind.BACKGROUND else page.redraw_pose


def clear_redraw_flag(session: Session, page_id: int, kind: GenKind) -> None:
    """「繼續做」排到這頁這種素材時清掉「要重畫」（UPDATE，不 commit：與排隊同一個交易）。"""
    column = Page.redraw_background if kind == GenKind.BACKGROUND else Page.redraw_pose
    session.exec(update(Page).where(col(Page.id) == page_id).values({column: False}))


class BookState:
    """批次判斷用：整本書每頁的背景／角色動作生成紀錄，以及選了舊流程整頁候選圖的頁面。"""

    def __init__(self, session: Session, book_id: int):
        self.pages = session.exec(
            select(Page).where(Page.book_id == book_id).order_by(col(Page.position), col(Page.id))
        ).all()
        self.gens: dict[GenKind, dict[int, list[Generation]]] = {
            GenKind.BACKGROUND: {}, GenKind.POSE: {}
        }
        for g in session.exec(
            select(Generation).where(
                Generation.book_id == book_id,
                col(Generation.kind).in_([GenKind.BACKGROUND, GenKind.POSE]),
            )
        ).all():
            if g.page_id is not None:
                self.gens[g.kind].setdefault(g.page_id, []).append(g)
        selected_ids = [
            p.selected_generation_id for p in self.pages if p.selected_generation_id is not None
        ]
        self.legacy_selected = set(
            session.exec(
                select(Generation.id).where(
                    col(Generation.id).in_(selected_ids), Generation.kind == GenKind.PAGE
                )
            ).all()
        ) if selected_ids else set()

    def skip_reason(self, page: Page, kind: GenKind, *, redraw: bool = False) -> str | None:
        """該頁的這種素材為什麼不用排；要排回 None。
        redraw（「繼續做」）：標了「要重畫」的照排，除非同種素材還在排隊／執行（旗標留著等下一次）。"""
        bg = kind == GenKind.BACKGROUND
        if not (page.scene_prompt if bg else page.action_prompt).strip():
            return "沒有場景描述" if bg else "沒有動作描述"
        gens = self.gens[kind].get(page.id, [])
        active = any(g.status in (GenStatus.QUEUED, GenStatus.RUNNING) for g in gens)
        if redraw and redraw_flag(page, kind):
            return ("背景產生中" if bg else "角色動作產生中") if active else None
        slot = page.background_generation_id if bg else page.pose_generation_id
        if (
            slot is not None
            or page.selected_generation_id in self.legacy_selected  # 舊流程整頁候選圖
            or any(g.status == GenStatus.SUCCEEDED and not g.discarded and not is_candidate(g) for g in gens)
        ):
            return "已有背景" if bg else "已有角色動作"
        if active:
            return "背景產生中" if bg else "角色動作產生中"
        return None

    def plan(
        self, has_character: bool, upload_pages: set[int] = frozenset(), *, redraw: bool = False
    ) -> list[tuple[Page, GenKind, str | None]]:
        """依頁面順序、每頁先背景再角色動作：(頁面, 種類, 略過原因；要排為 None)。
        upload_pages 的頁面改用上傳圖片，背景略過；沒有角色時角色動作略過。redraw 見 skip_reason。"""
        out: list[tuple[Page, GenKind, str | None]] = []
        for page in self.pages:
            reason = ("改用上傳圖片" if page.id in upload_pages
                      else self.skip_reason(page, GenKind.BACKGROUND, redraw=redraw))
            out.append((page, GenKind.BACKGROUND, reason))
            reason = (NO_CHARACTER_SKIP if not page.include_character
                      else self.skip_reason(page, GenKind.POSE, redraw=redraw))
            if reason is None and not has_character:
                reason = "請先設定角色"
            out.append((page, GenKind.POSE, reason))
        return out

    def has_both_images(self, page: Page) -> bool:
        """背景與角色動作都有（已選，或有可用的成功生成讓頁面流程自動選）；舊流程整頁圖的頁面不算。
        上傳的與重畫主角的候選不是自動選圖的候選，不算。不放主角的頁只看背景。"""
        if page.selected_generation_id in self.legacy_selected:
            return False
        slots = [(GenKind.BACKGROUND, page.background_generation_id)]
        if page.include_character:
            slots.append((GenKind.POSE, page.pose_generation_id))
        for kind, slot in slots:
            if slot is None and not any(is_usable(g, kind, page.id) and g.engine != UPLOAD_ENGINE
                                        and not is_candidate(g)
                                        for g in self.gens[kind].get(page.id, [])):  # 同自動選圖的候選
                return False
        return True
