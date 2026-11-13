"""API 輸入／輸出結構。"""

import json
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from app.api.urls import generation_cutout_url, generation_image_url, lineart_url
from app.models import SQLITE_INT_MAX, SQLITE_INT_MIN, Book, CharacterCheck, Generation, GenKind, GenStatus, Page, PageStatus
from app.services import history
from app.services.autofix import MAX_STROKES, MAX_TOTAL_POINTS  # noqa: F401  (re-export)
from app.services.caption_text import normalize_caption
from app.services.compose import Placement
from app.services.engine import public_engine_error
from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.lineart_render import LINE_WIDTH_MAX, LINE_WIDTH_MIN, LineDetail
from app.services.qa import QA_MESSAGES, QAReport

from app.services.worker import WorkerState

if TYPE_CHECKING:
    from app.services.worker import GenerationWorker

# 請求裡的資料列 id：超出 SQLite INTEGER 的回 422（不然查詢時 OverflowError → 500）
DbId = Annotated[int, Field(ge=SQLITE_INT_MIN, le=SQLITE_INT_MAX)]


# 去除前後空白後再檢查長度，避免純空白字串通過
TitleStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DescriptionStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
ScenePromptStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)]
ActionPromptStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


def _caption_validator(limit: int, message: str):
    """頁面文字：先正規化（空白一律變一個空格、去控制／格式字元、去頭尾），空的變 None，超過 limit 個 code point（Python len）→ 422 帶 message。"""
    def check(v: str | None) -> str | None:
        if v is None:
            return None
        v = normalize_caption(v)
        if v is None:
            return None
        if len(v) > limit:
            raise PydanticCustomError("caption_too_long", message)
        return v
    return check


CaptionZhStr = Annotated[str | None, AfterValidator(_caption_validator(12, "中文字最多 12 個字"))]
CaptionEnStr = Annotated[str | None, AfterValidator(_caption_validator(28, "英文字最多 28 個字元"))]
ActionStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class BookCreate(BaseModel):
    title: TitleStr
    paper_size: PaperSize
    print_mode: PrintMode


class BookUpdate(BaseModel):
    title: TitleStr | None = None
    paper_size: PaperSize | None = None
    print_mode: PrintMode | None = None


class LayoutOut(BaseModel):
    content_px: tuple[int, int]
    generation_size: tuple[int, int]


class BookStats(BaseModel):
    pages: int = 0
    passed: int = 0
    # B 的每頁狀態（GET /books/{id}/overview 的 state）各幾頁，首頁書卡用；加起來等於 pages
    ready: int = 0
    attention: int = 0
    working: int = 0
    todo: int = 0


class ShareIn(BaseModel):
    shared: bool


class SharedBookOut(BaseModel):
    """別人分享的書（列表項目）：只含完成的頁面數與封面頁（第一個完成的頁）。"""

    id: int
    title: str
    author: str
    paper_size: PaperSize
    page_count: int
    cover_image_url: str  # 封面頁的分享圖（帶 ?v=）
    shared_at: datetime
    mine: bool


class SharedPageOut(BaseModel):
    id: int
    position: int
    image_url: str  # /api/shared/pages/{id}/image.png?v=…；印出來的樣子變了（線稿、文字、要印哪幾行）就換網址


class SharedBookDetailOut(BaseModel):
    id: int
    title: str
    author: str
    paper_size: PaperSize
    mine: bool
    pages: list[SharedPageOut]


class BookOut(BaseModel):
    id: int
    title: str
    paper_size: PaperSize
    print_mode: PrintMode
    character_description: str | None
    character_generation_id: int | None
    layout: LayoutOut
    stats: BookStats
    cover_url: str | None  # position 最小且有選圖的頁的 lineart_url
    shared_at: datetime | None = None  # null＝沒有分享
    mine: bool = True  # false＝別人的書（admin 看得到全部，只能刪自己的）
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_book(
        cls, book: Book, stats: BookStats | None = None, cover_url: str | None = None, mine: bool = True
    ) -> "BookOut":
        lay = compute_layout(book.paper_size, book.print_mode)
        return cls(
            id=book.id,
            title=book.title,
            paper_size=book.paper_size,
            print_mode=book.print_mode,
            character_description=book.character_description,
            character_generation_id=book.character_generation_id,
            layout=LayoutOut(
                content_px=lay.content_px,
                generation_size=lay.generation_size,
            ),
            stats=stats or BookStats(),
            cover_url=cover_url,
            shared_at=book.shared_at,
            mine=mine,
            created_at=book.created_at,
            updated_at=book.updated_at,
        )


class CharacterCandidatesCreate(BaseModel):
    description: DescriptionStr
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    confirm_change: bool = False  # 已設定角色時：使用者確認要換角色（A 的「再畫一隻」）


class CharacterSelect(BaseModel):
    generation_id: DbId
    confirm_change: bool = False  # 已設定角色時：使用者確認要換（既有的角色動作留著）


class RestoreIn(BaseModel):
    """C 的「還原成自動畫的那張」：背景、角色動作或線條（清掉使用者筆畫）。"""

    item: Literal["background", "pose", "lines"]


class GenerationOut(BaseModel):
    id: int
    book_id: int
    page_id: int | None
    kind: GenKind
    status: GenStatus
    discarded: bool
    user_text: str  # 使用者輸入的原文（角色描述或場景描述）
    width: int
    height: int
    image_url: str | None
    cutout_url: str | None  # pose：裁好的去背 RGBA 圖
    source: Literal["generated", "upload"]  # engine == "upload"：使用者上傳的圖
    error: str | None
    elapsed_s: float | None
    queue_position: int | None
    progress: tuple[int, int] | None
    created_at: datetime

    @classmethod
    def from_generation(
        cls,
        gen: Generation,
        *,
        queue_position: int | None = None,
        progress: tuple[int, int] | None = None,
        is_admin: bool = False,
    ) -> "GenerationOut":
        return cls(
            id=gen.id,
            book_id=gen.book_id,
            page_id=gen.page_id,
            kind=gen.kind,
            status=gen.status,
            discarded=gen.discarded,
            user_text=gen.user_text,
            width=gen.width,
            height=gen.height,
            image_url=generation_image_url(gen.id) if gen.image_path else None,
            cutout_url=generation_cutout_url(gen.id) if gen.mask_path else None,
            source="upload" if gen.engine == "upload" else "generated",
            error=public_engine_error(gen.error, is_admin),
            elapsed_s=gen.elapsed_s,
            queue_position=queue_position,
            progress=progress,
            created_at=gen.created_at,
        )

    @classmethod
    def for_worker(cls, gen: Generation, worker: "GenerationWorker", *, is_admin: bool = False) -> "GenerationOut":
        """附上 worker 的排隊位置與進度。"""
        return cls.from_generation(
            gen, queue_position=worker.queue_position(gen.id), progress=worker.progress.get(gen.id),
            is_admin=is_admin,
        )

    @classmethod
    def for_worker_many(
        cls, gens: list[Generation], worker: "GenerationWorker", *, is_admin: bool = False
    ) -> list["GenerationOut"]:
        """for_worker 的批次版：排隊位置一次算完。"""
        positions = worker.queue_positions([g.id for g in gens])
        return [
            cls.from_generation(g, queue_position=positions[g.id], progress=worker.progress.get(g.id),
                                is_admin=is_admin)
            for g in gens
        ]


class PageCreate(BaseModel):
    scene_prompt: ScenePromptStr = ""
    action_prompt: ActionPromptStr = ""


class PageUpdate(BaseModel):
    scene_prompt: ScenePromptStr | None = None
    action_prompt: ActionPromptStr | None = None
    caption_zh: CaptionZhStr = None  # 未送＝不動（見 model_fields_set）；""、空白、null＝清除
    caption_en: CaptionEnStr = None
    # 要印哪幾行（未送＝不動；只收 true／false，null 也是 422）
    caption_show_zh: StrictBool = None  # type: ignore[assignment]
    caption_show_en: StrictBool = None  # type: ignore[assignment]
    line_detail: LineDetail | None = None
    line_width_px: int | None = Field(default=None, ge=LINE_WIDTH_MIN, le=LINE_WIDTH_MAX)


class CaptionDisplay(BaseModel):
    """整本書套用同一個「要印哪幾行」：兩個都 false＝每頁圖下方留白給小朋友寫。"""

    show_zh: StrictBool
    show_en: StrictBool


class PageOrder(BaseModel):
    page_ids: list[DbId]


class SelectGeneration(BaseModel):
    generation_id: DbId


class PoseCreate(BaseModel):
    action: ActionStr  # 動作文字（不帶場景描述）


class BackgroundCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 沒有參數；舊客戶端送的 style 等欄位忽略


class PagePlacement(BaseModel):
    """角色在背景上的位置（相對背景）：cx、cy 為角色中心，height 為角色高度／背景高度。"""

    cx: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    cy: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    height: float = Field(ge=0.1, le=1.0, allow_inf_nan=False)
    flip: bool = False

    def to_placement(self) -> Placement:
        return Placement(cx=self.cx, cy=self.cy, height=self.height, flip=self.flip)

    @classmethod
    def from_json(cls, text: str | None) -> "PagePlacement | None":
        return None if not text else cls(**Placement.from_dict(json.loads(text)).to_dict())


class PageOut(BaseModel):
    id: int
    book_id: int
    position: int
    scene_prompt: str
    action_prompt: str
    caption_zh: str | None
    caption_en: str | None
    caption_show_zh: bool  # 要印中文這行（開著但沒字＝不印）
    caption_show_en: bool  # 要印英文這行；兩個都 false＝圖下方留空白文字帶
    selected_generation_id: int | None
    background_generation_id: int | None
    pose_generation_id: int | None
    placement: PagePlacement | None  # 位置草稿；None＝還沒存過（前端用預設位置）
    status: PageStatus
    line_detail: LineDetail
    line_width_px: int
    character_check: CharacterCheck | None
    lineart_url: str | None  # 有選圖才有；?v= 為目前輸入對應的線稿 key
    needs_fix: bool | None  # 目前線稿的 QA 有錯誤；還沒算過（或參數已改、尚未重算）為 None
    aspect_mismatch: bool  # 選定圖的寬高 ≠ 繪本目前的 generation_size
    # 已選背景與角色動作，但選定圖不是用目前背景＋角色動作＋位置合成的（需要按「確認合成」）
    compose_pending: bool
    # Phase 13：「請看一下」（目前只有 "failed"）與中文說明；None＝不需要
    attention: str | None
    attention_detail: str | None
    # 頁面流程自動選的背景／角色動作（C 的「還原成自動畫的那張」：與目前選的不同時顯示）
    auto_background_generation_id: int | None
    auto_pose_generation_id: int | None
    has_user_strokes: bool  # 有使用者自己畫／擦的線（C 的線條「還原成自動畫的那張」）
    # 使用者筆畫等著頁面流程重新合成後寫回（C 這時停用畫筆；attention="failed" 時要先「再試一次」）
    recompose_pending: bool
    can_undo: bool  # C 的「↶ 復原」／「↷ 重做」
    can_redo: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_page(
        cls,
        page: Page,
        *,
        current_lineart_key: str | None = None,
        aspect_mismatch: bool = False,
        compose_pending: bool = False,
        has_user_strokes: bool = False,
        can_undo: bool = False,
        can_redo: bool = False,
    ) -> "PageOut":
        """current_lineart_key：依目前選圖／參數／修改圖層算出的 key（pages.page_outs 會給）。
        沒給時退回頁面上快取的 key，needs_fix 直接看 qa_json。"""
        selected = page.selected_generation_id is not None
        key = current_lineart_key or page.lineart_key
        needs_fix: bool | None = None
        if selected and page.qa_json and (
            current_lineart_key is None or page.lineart_key == current_lineart_key
        ):
            needs_fix = bool(json.loads(page.qa_json).get("errors"))
        return cls(
            id=page.id,
            book_id=page.book_id,
            position=page.position,
            scene_prompt=page.scene_prompt,
            action_prompt=page.action_prompt,
            caption_zh=page.caption_zh,
            caption_en=page.caption_en,
            caption_show_zh=page.caption_show_zh,
            caption_show_en=page.caption_show_en,
            selected_generation_id=page.selected_generation_id,
            background_generation_id=page.background_generation_id,
            pose_generation_id=page.pose_generation_id,
            placement=PagePlacement.from_json(page.placement_json),
            status=page.status,
            line_detail=page.line_detail,
            line_width_px=page.line_width_px,
            character_check=page.character_check,
            lineart_url=lineart_url(page.id, key) if selected else None,
            needs_fix=needs_fix,
            aspect_mismatch=aspect_mismatch if selected else False,
            compose_pending=compose_pending,
            attention=page.attention,
            attention_detail=page.attention_detail,
            auto_background_generation_id=page.auto_background_generation_id,
            auto_pose_generation_id=page.auto_pose_generation_id,
            has_user_strokes=has_user_strokes,
            # 少了背景或角色動作就合成不了，不算等著（舊資料可能留著 "[]"；角色動作好了流程照常合成、清掉）
            recompose_pending=page.pending_user_strokes is not None and history.has_both_slots(page),
            can_undo=can_undo,
            can_redo=can_redo,
            created_at=page.created_at,
            updated_at=page.updated_at,
        )


class RetryOut(PageOut):
    """C 的「再試一次」：頁面，加上這次做了什麼（重排了哪幾種圖、有沒有重跑頁面流程）。"""

    requeued: list[Literal["background", "pose"]]
    pipeline: bool


# ---- 修改圖層與 QA ---------------------------------------------------------------

STROKE_WIDTH_MIN, STROKE_WIDTH_MAX = 2, 64
MAX_STROKE_POINTS = 2000
# MAX_STROKES／MAX_TOTAL_POINTS（每一層的上限）定義在 services/autofix.py，頁面流程也用

# 相對座標：有限浮點數、落在 [0, 1]（"NaN"/"inf" 字串也會被 float 接受，所以明確拒絕非有限值）
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class StrokeIn(BaseModel):
    op: Literal["draw", "erase"]
    width_px: float = Field(ge=STROKE_WIDTH_MIN, le=STROKE_WIDTH_MAX, allow_inf_nan=False)
    points: list[tuple[UnitFloat, UnitFloat]] = Field(min_length=1, max_length=MAX_STROKE_POINTS)


class EditsIn(BaseModel):
    generation_id: DbId
    strokes: list[StrokeIn] = Field(max_length=MAX_STROKES)

    @model_validator(mode="after")
    def _total_points(self) -> "EditsIn":
        total = sum(len(s.points) for s in self.strokes)
        if total > MAX_TOTAL_POINTS:
            raise ValueError(f"修改圖層全部最多 {MAX_TOTAL_POINTS} 點，收到 {total}")
        return self


class StrokeOut(BaseModel):
    op: Literal["draw", "erase"]
    width_px: float
    points: list[tuple[float, float]]


class EditsOut(BaseModel):
    generation_id: int
    strokes: list[StrokeOut]


class AutofixIn(BaseModel):
    generation_id: DbId


class QAMessage(BaseModel):
    code: str
    message: str


def _messages(codes: list[str]) -> list[QAMessage]:
    return [QAMessage(code=c, message=QA_MESSAGES.get(c, c)) for c in codes]


class QAOut(BaseModel):
    status: PageStatus
    errors: list[QAMessage]
    warnings: list[QAMessage]
    gaps: list[tuple[float, float]]  # 相對座標 (x/w, y/h)
    gap_count: int
    character_check: CharacterCheck | None
    qa_note: str
    forced_pass_reason: str | None
    lineart_url: str

    @classmethod
    def build(cls, page: Page, qa: QAReport, key: str, **extra: Any) -> "QAOut":
        return cls(
            status=page.status,
            errors=_messages(qa.errors),
            warnings=_messages(qa.warnings),
            gaps=list(qa.gaps),
            gap_count=qa.gap_count,
            character_check=page.character_check,
            qa_note=page.qa_note,
            forced_pass_reason=page.forced_pass_reason,
            lineart_url=lineart_url(page.id, key),
            **extra,
        )


class AutofixOut(QAOut):
    autofix_added: int


class BatchSkip(BaseModel):
    kind: Literal["background", "pose"]
    page_id: int
    position: int
    reason: str


class BatchMaterialsIn(BaseModel):
    upload_page_ids: list[DbId] = Field(default_factory=list, max_length=500)  # 這些頁改用上傳圖片：不排背景


class BatchMaterialsOut(BaseModel):
    queued: list[GenerationOut]
    skipped: list[BatchSkip]


# ---- 開始做這本書（/produce）與 B 整本書（/overview）---------------------------------


class ProducePlanOut(BaseModel):
    backgrounds: int  # AI 背景：有場景描述、沒選背景（也沒上傳）、沒有排隊中的
    poses: int  # 角色動作：有動作描述、沒選、沒有排隊中的（要先有角色）
    total: int
    estimate_s: int  # 這次按下去會做的張數（min(total, fits)）× 每張約 22 秒
    remaining: int | None  # 今天還能排幾次；admin 不設上限為 None
    fits: int  # total 裡今天放得下的張數


class ProduceOut(BaseModel):
    queued: list[int]  # 新排的生成 id（依頁面順序，每頁先背景再角色動作）
    deferred_pages: list[int]  # 今天放不下、明天繼續的頁面 id（依頁面順序）
    pipeline_pages: list[int]  # 已有兩張圖、排了頁面流程的頁面 id（不計次數）


class OverviewPageOut(BaseModel):
    id: int
    position: int
    state: Literal["ready", "attention", "working", "todo"]
    gap_count: int | None  # 快取的 QA 是目前線稿的才有（GET 不算線稿）
    caption_zh: str | None
    caption_en: str | None
    caption_show_zh: bool
    caption_show_en: bool
    thumb_url: str | None  # 選定圖（合成圖；舊書是整頁圖）
    lineart_url: str | None  # 目前線稿（?v= 為目前的 key）；沒選定圖為 None
    attention_detail: str | None  # 有 attention 時的中文說明（review_pose 也有）
    review: bool  # attention == "review_pose"：換了新的主角動作（軟標記，state 照樣是 ready）


class RunProgress(BaseModel):
    queued: int  # 現在排隊中的生成＋頁面流程（不論哪一輪）
    running: int
    done: int  # 本輪（produce_started_at 之後建立）已結束的生成（不含合成圖、上傳）＋頁面流程


class ProgressPages(BaseModel):
    ready: int  # state == "ready" 的頁（PASSED、不在製作中、沒有請看一下；review_pose 也算）
    total: int  # 頁數


class BookOverviewOut(BaseModel):
    book_id: int
    pages: list[OverviewPageOut]
    progress: RunProgress  # 工作計數：ETA 與「製作中」用（不再當作「做好 N / M」）
    progress_pages: ProgressPages  # B 的「做好 N / M 頁」
    estimate_s: int  # 排隊中與執行中的生成 × 每張約 22 秒（頁面流程很快，不算）
    # 還有素材沒排（也沒有排隊中的那種）的頁數：上一輪放不下的（「還有 N 頁明天繼續」），沒按過時是全部缺的頁
    deferred_count: int
    run_started_at: datetime | None  # 最近一輪的開始時間；null＝還沒按過「開始做這本書」


InviteNameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class MeOut(BaseModel):
    name: str
    is_admin: bool
    # 只靠 auth.dev_owner（沒有 cookie）當作這個 admin：邀請管理不開放（見 deps.require_admin_hidden）
    via_dev_owner: bool = False


class InviteCreate(BaseModel):
    name: InviteNameStr
    is_admin: bool = False
    daily_generation_cap: int = Field(60, ge=1, le=1000)


class InviteOut(BaseModel):
    """管理頁的一筆邀請。link＝完整連結；舊資料、金鑰不對或已撤銷時為 null。"""

    id: int
    name: str
    is_admin: bool
    daily_generation_cap: int | None  # null＝不設上限（管理者）
    today: int  # 今天（台北）已排入的生成數
    book_count: int
    last_seen: datetime | None
    created_at: datetime
    revoked: bool
    link: str | None


# ---- 生成紀錄與佇列 ---------------------------------------------------------------

class GenerationUpdate(BaseModel):
    discarded: bool


class PendingJob(BaseModel):
    kind: Literal["character", "background", "pose"]


class RecentJob(BaseModel):
    kind: Literal["character", "background", "pose"]
    elapsed_s: float


class QueueStatus(BaseModel):
    state: WorkerState
    last_error: str | None
    queued: int
    # 供狀態膠囊估算剩餘時間：排隊中／執行中（舊→新），與各類型最近成功的耗時（新→舊）。
    pending: list[PendingJob]
    recent: list[RecentJob]


# ---- 角色檢查與通過 ---------------------------------------------------------------

NoteStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
ReasonStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]

class CharacterCheckIn(BaseModel):
    generation_id: int
    result: CharacterCheck
    note: NoteStr = ""


class PassIn(BaseModel):
    force_reason: ReasonStr | None = None
