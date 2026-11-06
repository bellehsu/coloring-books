import io

import pytest
from PIL import Image

from app.db import init_db, make_engine, session_factory
from app.models import Book, GenKind, Generation, GenStatus, Page, PageStatus
from app.services.layout import PaperSize, PrintMode
from app.storage import ImageRejected, Storage, normalize_png


def _img_bytes(size, fmt):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(buf, fmt)
    return buf.getvalue()


def test_tables_roundtrip(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    init_db(engine)
    make = session_factory(engine)
    with make() as s:
        book = Book(title="書", paper_size=PaperSize.A4, print_mode=PrintMode.HOME)
        s.add(book)
        s.commit()
        page = Page(book_id=book.id, position=1)
        gen = Generation(
            book_id=book.id, kind=GenKind.PAGE, user_text="u", prompt="p",
            negative_prompt="n", seed=1, width=64, height=64, request_json="{}",
        )
        s.add(page)
        s.add(gen)
        s.commit()
        bid, pid, gid = book.id, page.id, gen.id
    with make() as s:
        book = s.get(Book, bid)
        assert isinstance(book.paper_size, PaperSize)
        assert isinstance(book.print_mode, PrintMode)
        assert book.created_at is not None
        assert book.print_mode is PrintMode.HOME
        page = s.get(Page, pid)
        assert page.controlnet_strength == 0.4
        assert page.status is PageStatus.DRAFT
        gen = s.get(Generation, gid)
        assert gen.status is GenStatus.QUEUED
        assert gen.kind is GenKind.PAGE
        assert gen.discarded is False


def test_storage_paths_and_write(tmp_path):
    st = Storage(tmp_path)
    assert st.generation_image(3, 7) == "books/3/generations/7.png"
    assert st.sketch(3, 5) == "books/3/pages/5/sketch.png"
    p = st.write(st.generation_image(3, 7), b"abc")
    assert p == st.abs("books/3/generations/7.png")
    assert p.read_bytes() == b"abc"
    st.delete("books/3/generations/7.png")
    assert not p.exists()
    st.delete("books/3/generations/7.png")


def test_normalize_png_rejects():
    with pytest.raises(ImageRejected):
        normalize_png(b"not an image")
    with pytest.raises(ImageRejected):
        normalize_png(_img_bytes((100, 100), "PNG"), max_bytes=10)
    with pytest.raises(ImageRejected):
        normalize_png(_img_bytes((32, 32), "PNG"))
    with pytest.raises(ImageRejected):
        normalize_png(_img_bytes((200, 100), "PNG"), max_side=150)
    data, size = normalize_png(_img_bytes((128, 96), "JPEG"))
    assert data.startswith(b"\x89PNG")
    assert size == (128, 96)
