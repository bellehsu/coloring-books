"""API 輸入／輸出結構。"""

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models import SQLITE_INT_MAX, SQLITE_INT_MIN, Book, CharacterCheck, Generation, GenKind, GenStatus, Page, PageStatus
from app.services.compose import Placement
from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.lineart_render import LINE_WIDTH_MAX, LINE_WIDTH_MIN, LineDetail
from app.services.qa import QA_MESSAGES, QAReport

# 請求裡的資料列 id：超出 SQLite INTEGER 的回 422（不然查詢時 OverflowError → 500）
DbId = Annotated[int, Field(ge=SQLITE_INT_MIN, le=SQLITE_INT_MAX)]


# 去除前後空白後再檢查長度，避免純空白字串通過
TitleStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DescriptionStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
ScenePromptStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)]
ActionPromptStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
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
    paper_mm: tuple[float, float]
    content_mm: tuple[float, float]
    content_px: tuple[int, int]
    generation_size: tuple[int, int]
    safe_inset_mm: float | None
    page_px: tuple[int, int]
    content_offset_px: tuple[int, int]
    trim_box_px: tuple[int, int, int, int] | None
    safe_box_px: tuple[int, int, int, int] | None
    ratio_group: str


class BookStats(BaseModel):
    pages: int = 0
    passed: int = 0
    needs_fix: int = 0  # 已選圖且目前 QA 有錯誤的頁數
    no_selection: int = 0


class ShareIn(BaseModel):
    shared: bool


class SharedBookOut(BaseModel):
    """別人分享的書（列表項目）：只含完成的頁面數與封面頁（第一個完成的頁）。"""

    id: int
    title: str
    author: str
    paper_size: PaperSize
    page_count: int
    cover_page_id: int
    shared_at: datetime
    mine: bool


class SharedPageOut(BaseModel):
    id: int
    position: int


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
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_book(
        cls, book: Book, stats: BookStats | None = None, cover_url: str | None = None
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
                paper_mm=lay.paper_mm,
                content_mm=lay.content_mm,
                content_px=lay.content_px,
                generation_size=lay.generation_size,
                safe_inset_mm=lay.safe_inset_mm,
                page_px=lay.page_px,
                content_offset_px=lay.content_offset_px,
                trim_box_px=lay.trim_box_px,
                safe_box_px=lay.safe_box_px,
                ratio_group=lay.ratio_group,
            ),
            stats=stats or BookStats(),
            cover_url=cover_url,
            shared_at=book.shared_at,
            created_at=book.created_at,
            updated_at=book.updated_at,
        )


class CharacterCandidatesCreate(BaseModel):
    description: DescriptionStr
    count: int = Field(default=4, ge=1, le=8)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class CharacterSelect(BaseModel):
    generation_id: DbId


# 角色動作模式與背景風格：新列一律 precise／simple（Qwen）；fast／detailed 只出現在 Phase 9 以前的 SDXL 舊列，
# GenerationOut 照舊回報，前端才能替舊縮圖標「快速」／「細緻」。
PoseMode = Literal["precise", "fast"]
BackgroundStyle = Literal["simple", "detailed"]


def pose_mode_of(gen: Generation) -> PoseMode | None:
    """角色動作的模式（看 request_json，排隊中、還沒有 workflow_name 也算得出來）；
    其他 kind 為 None。Phase 5 的舊列沒有 pose_mode（或內容讀不懂）→ "fast"。"""
    if gen.kind != GenKind.POSE:
        return None
    try:
        mode = json.loads(gen.request_json or "{}").get("pose_mode")
    except (ValueError, AttributeError):
        return "fast"
    return "precise" if mode == "precise" else "fast"


def background_style_of(gen: Generation) -> BackgroundStyle | None:
    """背景的實際風格（看 request_json，排隊中也算得出來）；其他 kind 為 None。
    背景風格上線前的舊列沒有 background_style（或內容讀不懂）→ "detailed"。上傳的圖片沒有風格 → None。"""
    if gen.kind != GenKind.BACKGROUND or gen.engine == "upload":
        return None
    try:
        style = json.loads(gen.request_json or "{}").get("background_style")
    except (ValueError, AttributeError):
        return "detailed"
    return "simple" if style == "simple" else "detailed"


class GenerationOut(BaseModel):
    id: int
    book_id: int
    page_id: int | None
    kind: GenKind
    status: GenStatus
    discarded: bool
    prompt: str
    user_text: str  # 使用者輸入的原文（角色描述或場景描述）
    seed: int
    width: int
    height: int
    image_url: str | None
    mask_url: str | None  # pose：去背遮罩
    cutout_url: str | None  # pose：裁好的去背 RGBA 圖
    pose_mode: PoseMode | None  # pose：精準／快速；其他 kind 為 None
    background_style: BackgroundStyle | None  # background：實際風格（簡潔／細緻）；其他 kind 為 None
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
    ) -> "GenerationOut":
        return cls(
            id=gen.id,
            book_id=gen.book_id,
            page_id=gen.page_id,
            kind=gen.kind,
            status=gen.status,
            discarded=gen.discarded,
            prompt=gen.prompt,
            user_text=gen.user_text,
            seed=gen.seed,
            width=gen.width,
            height=gen.height,
            image_url=f"/api/generations/{gen.id}/image" if gen.image_path else None,
            mask_url=f"/api/generations/{gen.id}/mask" if gen.mask_path else None,
            cutout_url=f"/api/generations/{gen.id}/cutout" if gen.mask_path else None,
            pose_mode=pose_mode_of(gen),
            background_style=background_style_of(gen),
            source="upload" if gen.engine == "upload" else "generated",
            error=gen.error,
            elapsed_s=gen.elapsed_s,
            queue_position=queue_position,
            progress=progress,
            created_at=gen.created_at,
        )


class PageCreate(BaseModel):
    scene_prompt: ScenePromptStr = ""
    action_prompt: ActionPromptStr = ""


class PageUpdate(BaseModel):
    scene_prompt: ScenePromptStr | None = None
    action_prompt: ActionPromptStr | None = None
    controlnet_strength: float | None = None  # 構圖草圖已停用（0006 已移除欄位）：舊前端會送，照收但忽略
    line_detail: LineDetail | None = None
    line_width_px: int | None = Field(default=None, ge=LINE_WIDTH_MIN, le=LINE_WIDTH_MAX)


class PageOrder(BaseModel):
    page_ids: list[DbId]


class SelectGeneration(BaseModel):
    generation_id: DbId


class PoseCreate(BaseModel):
    action: ActionStr  # 動作文字（不帶場景描述）
    mode: str | None = None  # 舊前端會送精準／快速；忽略（只有 Qwen 一種）


class BackgroundCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    style: str | None = None  # 舊前端會送簡潔／細緻；忽略（只有 Qwen 一種）


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


def lineart_url(page_id: int, key: str | None) -> str:
    return f"/api/pages/{page_id}/lineart" + (f"?v={key}" if key else "")


class PageOut(BaseModel):
    id: int
    book_id: int
    position: int
    scene_prompt: str
    action_prompt: str
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
            created_at=page.created_at,
            updated_at=page.updated_at,
        )


# ---- 修改圖層與 QA ---------------------------------------------------------------

STROKE_WIDTH_MIN, STROKE_WIDTH_MAX = 2, 64
MAX_STROKE_POINTS = 2000
MAX_STROKES = 500
MAX_TOTAL_POINTS = 20000

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
    dangling_count: int
    near_miss_count: int
    metrics: dict[str, Any]
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
            dangling_count=qa.dangling_count,
            near_miss_count=qa.near_miss_count,
            metrics=qa.metrics,
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


InviteNameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class MeOut(BaseModel):
    name: str
    is_admin: bool


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
