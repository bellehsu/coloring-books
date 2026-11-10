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


def _png_header_only(width: int, height: int) -> bytes:
    """只有 PNG 檔頭（IHDR 宣告的尺寸）＋空 IDAT／IEND：Pillow 開得起來，但解碼會失敗。"""
    import struct
    import zlib

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


def test_normalize_png_too_many_pixels_rejected_before_decoding(monkeypatch):
    from PIL import ImageFile

    from app.storage import UPLOAD_MAX_PIXELS, UPLOAD_TOO_MANY_PIXELS, TooManyPixels

    def no_decode(self):
        raise AssertionError("不應該解碼")

    monkeypatch.setattr(ImageFile.ImageFile, "load", no_decode)
    buf = io.BytesIO()
    Image.new("1", (8001, 8000), 1).save(buf, "PNG")  # 6400.8 萬像素，單色壓得很小
    assert len(buf.getvalue()) < 1024 * 1024
    for data in (buf.getvalue(), _png_header_only(100_000, 100_000), _png_header_only(8000, 8001)):
        with pytest.raises(TooManyPixels) as ei:
            normalize_png(data, max_side=200_000, max_pixels=UPLOAD_MAX_PIXELS)
        assert str(ei.value) == UPLOAD_TOO_MANY_PIXELS == "圖片像素太多（最多 6400 萬像素）"
        assert isinstance(ei.value, ImageRejected)


def test_normalize_png_pixel_limit_is_inclusive():
    from app.storage import TooManyPixels

    data = _img_bytes((100, 64), "PNG")
    assert normalize_png(data, max_pixels=6400)[1] == (100, 64)
    with pytest.raises(TooManyPixels):
        normalize_png(data, max_pixels=6399)


def _gray_mean(im: Image.Image) -> float:
    import numpy as np

    return float(np.asarray(im.convert("L"), dtype=np.float64).mean())


def test_normalize_png_16bit_grayscale_keeps_tones():
    """16-bit 灰階 PNG（Pillow 讀成 I;16）直接 convert("RGB") 會被截到 255 → 幾乎全白。"""
    import numpy as np

    ramp = np.tile(np.linspace(0, 65535, 512).round().astype(np.uint16), (256, 1))
    src = Image.fromarray(ramp)
    assert src.mode == "I;16"
    buf = io.BytesIO()
    src.save(buf, "PNG")
    data, size = normalize_png(buf.getvalue())
    out = Image.open(io.BytesIO(data))
    assert out.mode == "RGB" and size == (512, 256)
    assert abs(_gray_mean(out) - 127.5) < 2
    assert out.getpixel((0, 0))[0] < 5 and out.getpixel((511, 0))[0] > 250


@pytest.mark.parametrize("make,expected_mean", [
    # mode I：0..65535 → 縮成 8-bit
    (lambda np: np.tile(np.linspace(0, 65535, 600).round().astype(np.int32), (600, 1)), 127.5),
    # mode I：值都在 0..255 → 原樣
    (lambda np: np.tile(np.linspace(0, 255, 600).round().astype(np.int32), (600, 1)), 127.5),
    # mode I：負值截到 0
    (lambda np: np.full((600, 600), -500, dtype=np.int32), 0.0),
    # mode F：0..1 → 0..255
    (lambda np: np.tile(np.linspace(0, 1, 600).astype(np.float32), (600, 1)), 127.5),
    # mode F：超過 1 的依最大值正規化
    (lambda np: np.tile(np.linspace(0, 4000, 600).astype(np.float32), (600, 1)), 127.5),
])
def test_flatten_on_white_scales_high_bit_depth_modes(make, expected_mean):
    import numpy as np

    from app.storage import _flatten_on_white

    im = Image.fromarray(make(np))
    assert im.mode in ("I", "F")
    out = _flatten_on_white(im)
    assert out.mode == "RGB"
    assert abs(_gray_mean(out) - expected_mean) < 2


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


def test_load_rgb_returns_image_and_drafts_jpeg():
    """load_rgb 回傳 RGB Image（不經 PNG 編碼）；給 draft_size 時 JPEG 縮小解碼，但每邊不小於要求。"""
    from app.storage import load_rgb

    im, size = load_rgb(_img_bytes((2000, 1600), "JPEG"), draft_size=(400, 300))
    assert size == (2000, 1600)
    assert im.mode == "RGB"
    assert im.width < 2000 and im.width >= 400 and im.height >= 300
    im, size = load_rgb(_img_bytes((300, 200), "PNG"), draft_size=(100, 100))
    assert size == (300, 200) and im.size == (300, 200)


def test_load_rgb_draft_respects_exif_rotation():
    """EXIF 轉 90° 的 JPEG：回報轉正後的原尺寸，縮小解碼後仍蓋得住要求的（轉正後）尺寸。"""
    from app.storage import load_rgb

    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # 順時針轉 90°
    Image.new("RGB", (2400, 1200), "white").save(buf, "JPEG", exif=exif.tobytes())
    im, size = load_rgb(buf.getvalue(), draft_size=(200, 500))
    assert size == (1200, 2400)
    assert im.width < 1200 and im.width >= 200 and im.height >= 500
    assert im.height > im.width
