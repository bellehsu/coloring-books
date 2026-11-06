"""繪本與角色端點。"""

import random

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select

from app.api.deps import get_session, get_worker
from app.api.schemas import (
    BookCreate,
    BookOut,
    BookUpdate,
    CharacterCandidatesCreate,
    CharacterSelect,
    GenerationOut,
)
from app.models import Book, Generation, GenKind, GenStatus, utcnow
from app.services.prompts import CHARACTER_NEGATIVE, character_prompt
from app.services.worker import GenerationWorker, encode_request

router = APIRouter(prefix="/api/books", tags=["books"])

CHARACTER_SIZE = (832, 1216)


def _book_or_404(session: Session, book_id: int) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "找不到這本繪本")
    return book


@router.post("", status_code=201, response_model=BookOut)
def create_book(body: BookCreate, session: Session = Depends(get_session)) -> BookOut:
    book = Book(title=body.title, paper_size=body.paper_size, print_mode=body.print_mode)
    session.add(book)
    session.commit()
    return BookOut.from_book(book)


@router.get("", response_model=list[BookOut])
def list_books(session: Session = Depends(get_session)) -> list[BookOut]:
    books = session.exec(select(Book).order_by(col(Book.id).desc())).all()
    return [BookOut.from_book(b) for b in books]


@router.get("/{book_id}", response_model=BookOut)
def get_book(book_id: int, session: Session = Depends(get_session)) -> BookOut:
    return BookOut.from_book(_book_or_404(session, book_id))


@router.patch("/{book_id}", response_model=BookOut)
def update_book(book_id: int, body: BookUpdate, session: Session = Depends(get_session)) -> BookOut:
    book = _book_or_404(session, book_id)
    book.title = body.title
    book.updated_at = utcnow()
    session.add(book)
    session.commit()
    return BookOut.from_book(book)


@router.post("/{book_id}/character/candidates", status_code=202, response_model=list[GenerationOut])
def create_character_candidates(
    book_id: int,
    body: CharacterCandidatesCreate,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
) -> list[GenerationOut]:
    book = _book_or_404(session, book_id)
    if book.character_generation_id is not None:
        raise HTTPException(409, "這本繪本的角色已經設定，不能再產生候選圖")
    base = body.seed if body.seed is not None else random.randint(0, 2**32 - body.count)
    prompt = character_prompt(body.description)
    width, height = CHARACTER_SIZE
    gens = [
        Generation(
            book_id=book_id,
            kind=GenKind.CHARACTER,
            user_text=body.description,
            prompt=prompt,
            negative_prompt=CHARACTER_NEGATIVE,
            seed=base + i,
            width=width,
            height=height,
            request_json=encode_request(
                kind=GenKind.CHARACTER,
                prompt=prompt,
                negative_prompt=CHARACTER_NEGATIVE,
                seed=base + i,
                width=width,
                height=height,
            ),
        )
        for i in range(body.count)
    ]
    session.add_all(gens)
    session.commit()
    worker.notify()
    return [
        GenerationOut.from_generation(
            g, queue_position=worker.queue_position(g.id), progress=worker.progress.get(g.id)
        )
        for g in gens
    ]


@router.post("/{book_id}/character", response_model=BookOut)
def set_character(
    book_id: int, body: CharacterSelect, session: Session = Depends(get_session)
) -> BookOut:
    book = _book_or_404(session, book_id)
    if book.character_generation_id is not None:
        raise HTTPException(409, "這本繪本的角色已經設定")
    gen = session.get(Generation, body.generation_id)
    if gen is None:
        raise HTTPException(404, "找不到這筆生成紀錄")
    if gen.book_id != book_id:
        raise HTTPException(409, "這筆生成紀錄不屬於這本繪本")
    if gen.kind != GenKind.CHARACTER:
        raise HTTPException(409, "只能選擇角色候選圖")
    if gen.status != GenStatus.SUCCEEDED:
        raise HTTPException(409, "這張候選圖尚未成功生成")
    if gen.discarded:
        raise HTTPException(409, "這張候選圖已被捨棄")
    book.character_generation_id = gen.id
    book.character_description = gen.user_text
    book.updated_at = utcnow()
    session.add(book)
    session.commit()
    return BookOut.from_book(book)
