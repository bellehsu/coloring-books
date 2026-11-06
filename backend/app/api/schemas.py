"""API 輸入／輸出結構。"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models import Book, Generation, GenKind, GenStatus, Page, PageStatus
from app.services.layout import PaperSize, PrintMode, compute_layout


# 去除前後空白後再檢查長度，避免純空白字串通過
TitleStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DescriptionStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
ScenePromptStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)]


class BookCreate(BaseModel):
    title: TitleStr
    paper_size: PaperSize
    print_mode: PrintMode


class BookUpdate(BaseModel):
    title: TitleStr


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


class BookOut(BaseModel):
    id: int
    title: str
    paper_size: PaperSize
    print_mode: PrintMode
    character_description: str | None
    character_generation_id: int | None
    layout: LayoutOut
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_book(cls, book: Book) -> "BookOut":
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


class PageCandidatesCreate(BaseModel):
    count: int = Field(default=4, ge=1, le=8)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class PageOrder(BaseModel):
    page_ids: list[int]


class SelectGeneration(BaseModel):
    generation_id: int


class PageOut(BaseModel):
    id: int
    book_id: int
    position: int
    scene_prompt: str
    controlnet_strength: float
    sketch_url: str | None
    selected_generation_id: int | None
    status: PageStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_page(cls, page: Page) -> "PageOut":
        return cls(
            id=page.id,
            book_id=page.book_id,
            position=page.position,
            scene_prompt=page.scene_prompt,
            controlnet_strength=page.controlnet_strength,
            sketch_url=f"/api/pages/{page.id}/sketch" if page.sketch_path else None,
            selected_generation_id=page.selected_generation_id,
            status=page.status,
            created_at=page.created_at,
            updated_at=page.updated_at,
        )
