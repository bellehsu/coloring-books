"""API 輸入／輸出結構。"""

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.models import Book, CharacterCheck, Generation, GenKind, GenStatus, Page, PageStatus
from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.lineart_render import LINE_WIDTH_MAX, LINE_WIDTH_MIN, LineDetail
from app.services.qa import QA_MESSAGES, QAReport


# 去除前後空白後再檢查長度，避免純空白字串通過
TitleStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DescriptionStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
ScenePromptStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)]


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
            created_at=book.created_at,
            updated_at=book.updated_at,
        )


class CharacterCandidatesCreate(BaseModel):
    description: DescriptionStr
    count: int = Field(default=4, ge=1, le=8)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class CharacterSelect(BaseModel):
    generation_id: int


class GenerationOut(BaseModel):
    id: int
    book_id: int
    page_id: int | None
    kind: GenKind
    status: GenStatus
    discarded: bool
    prompt: str
    seed: int
    width: int
    height: int
    image_url: str | None
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
            seed=gen.seed,
            width=gen.width,
            height=gen.height,
            image_url=f"/api/generations/{gen.id}/image" if gen.image_path else None,
            error=gen.error,
            elapsed_s=gen.elapsed_s,
            queue_position=queue_position,
            progress=progress,
            created_at=gen.created_at,
        )


class PageCreate(BaseModel):
    scene_prompt: ScenePromptStr = ""


class PageUpdate(BaseModel):
    scene_prompt: ScenePromptStr | None = None
    controlnet_strength: float | None = Field(default=None, ge=0.0, le=0.6)
    line_detail: LineDetail | None = None
    line_width_px: int | None = Field(default=None, ge=LINE_WIDTH_MIN, le=LINE_WIDTH_MAX)


class PageCandidatesCreate(BaseModel):
    count: int = Field(default=4, ge=1, le=8)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class PageOrder(BaseModel):
    page_ids: list[int]


class SelectGeneration(BaseModel):
    generation_id: int


def lineart_url(page_id: int, key: str | None) -> str:
    return f"/api/pages/{page_id}/lineart" + (f"?v={key}" if key else "")


def _version(dt: datetime) -> int:
    """防快取用的版本號：微秒時間戳。"""
    return round(dt.timestamp() * 1_000_000)


class PageOut(BaseModel):
    id: int
    book_id: int
    position: int
    scene_prompt: str
    controlnet_strength: float
    sketch_url: str | None
    selected_generation_id: int | None
    status: PageStatus
    line_detail: LineDetail
    line_width_px: int
    character_check: CharacterCheck | None
    lineart_url: str | None  # 有選圖才有；?v= 為目前輸入對應的線稿 key
    needs_fix: bool | None  # 目前線稿的 QA 有錯誤；還沒算過（或參數已改、尚未重算）為 None
    aspect_mismatch: bool  # 選定圖的寬高 ≠ 繪本目前的 generation_size
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_page(
        cls,
        page: Page,
        *,
        current_lineart_key: str | None = None,
        aspect_mismatch: bool = False,
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
            controlnet_strength=page.controlnet_strength,
            sketch_url=(
                f"/api/pages/{page.id}/sketch?v={_version(page.updated_at)}"
                if page.sketch_path
                else None
            ),
            selected_generation_id=page.selected_generation_id,
            status=page.status,
            line_detail=page.line_detail,
            line_width_px=page.line_width_px,
            character_check=page.character_check,
            lineart_url=lineart_url(page.id, key) if selected else None,
            needs_fix=needs_fix,
            aspect_mismatch=aspect_mismatch if selected else False,
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
    generation_id: int
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
    generation_id: int


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
