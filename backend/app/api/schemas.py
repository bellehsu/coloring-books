"""API 輸入／輸出結構。"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models import Book, Generation, GenKind, GenStatus
from app.services.layout import PaperSize, PrintMode, compute_layout


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    paper_size: PaperSize
    print_mode: PrintMode


class BookUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class LayoutOut(BaseModel):
    content_mm: tuple[float, float]
    content_px: tuple[int, int]
    generation_size: tuple[int, int]
    safe_inset_mm: float | None


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
                content_mm=lay.content_mm,
                content_px=lay.content_px,
                generation_size=lay.generation_size,
                safe_inset_mm=lay.safe_inset_mm,
            ),
            created_at=book.created_at,
            updated_at=book.updated_at,
        )


class CharacterCandidatesCreate(BaseModel):
    description: str = Field(min_length=1, max_length=800)
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
