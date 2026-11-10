import io
from datetime import datetime, timezone

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
        assert book.created_at.tzinfo is not None
        assert book.created_at.utcoffset().total_seconds() == 0
        assert book.print_mode is PrintMode.HOME
        assert book.created_at.utcoffset().total_seconds() == 0
        page = s.get(Page, pid)
        assert page.controlnet_strength == 0.4
        assert page.status is PageStatus.DRAFT
        gen = s.get(Generation, gid)
        assert gen.status is GenStatus.QUEUED
        assert gen.kind is GenKind.PAGE
        assert gen.discarded is False
        assert gen.created_at.tzinfo is not None
        gen.started_at = datetime.now(timezone.utc)
        gen.finished_at = datetime.now(timezone.utc)
        s.add(gen)
        s.commit()
    with make() as s:
        gen = s.get(Generation, gid)
        assert gen.started_at.utcoffset().total_seconds() == 0
        assert (gen.finished_at - gen.started_at).total_seconds() >= 0
        assert (datetime.now(timezone.utc) - gen.finished_at).total_seconds() >= 0


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


def test_storage_rejects_escape(tmp_path):
    st = Storage(tmp_path / "root")
    for bad in ("../evil.png", "a/../../evil.png", "/etc/passwd"):
        with pytest.raises(ValueError):
            st.abs(bad)
        with pytest.raises(ValueError):
            st.write(bad, b"x")
        with pytest.raises(ValueError):
            st.delete(bad)
    assert not (tmp_path / "evil.png").exists()


def test_normalize_png_composites_alpha_on_white():
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(100):
        im.putpixel((x, 50), (0, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    data, size = normalize_png(buf.getvalue())
    out = Image.open(io.BytesIO(data))
    assert out.mode == "RGB"
    assert out.getpixel((0, 0)) == (255, 255, 255)
    assert out.getpixel((50, 50)) == (0, 0, 0)


def test_page_phase3_defaults_and_lineart_edit(tmp_path):
    from app.models import CharacterCheck, LineartEdit
    from app.services.lineart_render import LineDetail

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
        s.add(LineartEdit(generation_id=gen.id, page_id=page.id, strokes_json='[{"a":1}]'))
        page.passed_at = datetime(2026, 11, 6, tzinfo=timezone.utc)
        page.character_check = CharacterCheck.CONSISTENT
        s.add(page)
        s.commit()
        pid, gid = page.id, gen.id
    with make() as s:
        page = s.get(Page, pid)
        assert page.line_detail is LineDetail.STANDARD
        assert page.line_width_px == 11
        assert page.qa_note == ""
        assert page.forced_pass_reason is None and page.lineart_path is None
        assert page.character_check is CharacterCheck.CONSISTENT
        assert page.passed_at.tzinfo is not None
        edit = s.get(LineartEdit, gid)
        assert edit.strokes_json == '[{"a":1}]' and edit.updated_at.tzinfo is not None
        assert LineartEdit(generation_id=1, page_id=1).strokes_json == "[]"


def test_storage_lineart_and_preview_paths(tmp_path):
    st = Storage(tmp_path)
    assert st.lineart(3, 4, "abc") == "books/3/pages/4/lineart_abc.png"
    assert st.preview(9, "qa") == "cache/previews/9_qa.png"


def test_compose_kinds_and_fields_roundtrip(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    init_db(engine)
    make = session_factory(engine)
    with make() as s:
        book = Book(title="書", paper_size=PaperSize.A4, print_mode=PrintMode.HOME)
        s.add(book)
        s.commit()
        for kind in (GenKind.BACKGROUND, GenKind.POSE, GenKind.COMPOSITE):
            s.add(Generation(book_id=book.id, kind=kind, user_text="u", prompt="p", negative_prompt="n",
                             seed=1, width=8, height=8, request_json="{}", mask_path="m.png"))
        s.add(Page(book_id=book.id, position=1, background_generation_id=1, pose_generation_id=2,
                   placement_json='{"cx": 0.5}'))
        s.commit()
    with make() as s:
        from sqlmodel import select
        kinds = [g.kind for g in s.exec(select(Generation).order_by(Generation.id))]
        assert kinds == [GenKind.BACKGROUND, GenKind.POSE, GenKind.COMPOSITE]
        assert GenKind.BACKGROUND.value == "background" and GenKind.POSE.value == "pose"
        assert s.get(Generation, 1).mask_path == "m.png"
        p = s.exec(select(Page)).one()
        assert (p.background_generation_id, p.pose_generation_id, p.placement_json) == (1, 2, '{"cx": 0.5}')


def test_generation_mask_path():
    assert Storage("/x").generation_mask(3, 7) == "books/3/generations/7_mask.png"
