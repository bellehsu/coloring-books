import io

import pytest
from PIL import Image, ImageDraw

from app.db import init_db, make_engine, session_factory
from app.models import (Book, GenKind, Generation, GenStatus, LineartEdit, Owner, Page, PageStatus, StrokeLayer, utcnow)
from app.services import page_lineart
from app.services.layout import PaperSize, PrintMode
from app.services.lineart_render import Stroke
from app.services.qa import QAReport
from app.storage import LocalStorage
from sqlmodel import select


def _color_png() -> bytes:
    im = Image.new("RGB", (256, 256), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, 216, 216], fill=(250, 200, 200), outline=(0, 0, 0), width=4)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _report(errors):
    return QAReport(errors=errors, warnings=[], gaps=[], dangling_count=0, gap_count=0)


def _owner(s) -> int:
    o = Owner(name="t", token_hash="h" * 64)
    s.add(o)
    s.commit()
    return o.id


@pytest.fixture
def env(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    init_db(engine)
    make = session_factory(engine)
    storage = LocalStorage(tmp_path / "storage", b"test-signing-key")
    with make() as s:
        book = Book(title="書", paper_size=PaperSize.A4, print_mode=PrintMode.HOME, owner_id=_owner(s))
        s.add(book)
        s.commit()
        page = Page(book_id=book.id, position=1)
        s.add(page)
        s.commit()
        gen = Generation(
            book_id=book.id, page_id=page.id, kind=GenKind.PAGE, status=GenStatus.SUCCEEDED,
            user_text="u", prompt="p", negative_prompt="n", seed=1, width=256, height=256,
            request_json="{}",
        )
        s.add(gen)
        s.commit()
        gen.image_path = storage.generation_image(book.id, gen.id)
        storage.put(gen.image_path, _color_png())
        page.selected_generation_id = gen.id
        s.add_all([gen, page])
        s.commit()
        return make, storage, page.id, gen.id


def test_cached_until_inputs_change(env, monkeypatch):
    make, storage, pid, gid = env
    calls = []
    orig = page_lineart.render_base

    def counting(*a, **k):
        calls.append(1)
        return orig(*a, **k)

    monkeypatch.setattr(page_lineart, "render_base", counting)
    with make() as s:
        r1 = page_lineart.ensure_page_lineart(s, storage, pid)
        r2 = page_lineart.ensure_page_lineart(s, storage, pid)
        assert len(calls) == 1
        assert r1.key == r2.key and r1.path == r2.path
        assert storage.exists(r1.path)
        page = s.get(Page, pid)
        assert page.lineart_key == r1.key and page.qa_json
        page.line_width_px = 9
        s.add(page)
        s.commit()
        r3 = page_lineart.ensure_page_lineart(s, storage, pid)
        assert len(calls) == 2
        assert r3.key != r1.key


def test_strokes_change_key_and_output(env):
    make, storage, pid, gid = env
    with make() as s:
        r1 = page_lineart.ensure_page_lineart(s, storage, pid)
        before = storage.get(r1.path)
        page_lineart.save_strokes(
            s, pid, gid, [Stroke("draw", 12.0, ((0.1, 0.1), (0.9, 0.9)))], layer=StrokeLayer.USER
        )
        s.commit()
        assert len(page_lineart.load_strokes(s, gid, layer=StrokeLayer.USER)) == 1
        r2 = page_lineart.ensure_page_lineart(s, storage, pid)
        assert r2.key != r1.key
        assert storage.get(r2.path) != before


# 線稿上方的空白處（彩色方塊從 40/256 開始）：一條橫線，與在線上擦掉一塊
TOP_LINE = Stroke("draw", 16.0, ((0.1, 0.08), (0.9, 0.08)))
TOP_ERASE = Stroke("erase", 64.0, ((0.5, 0.08),))


def _ink_at(storage, path, x, y) -> bool:
    with Image.open(io.BytesIO(storage.get(path))) as im:
        g = im.convert("L")
        w, h = g.size
        return g.getpixel((round(x * (w - 1)), round(y * (h - 1)))) < 128


def test_stroke_layers_stored_separately(env):
    make, storage, pid, gid = env
    with make() as s:
        assert page_lineart.load_strokes(s, gid, layer=StrokeLayer.AUTO) == []
        page_lineart.save_strokes(s, pid, gid, [TOP_LINE], layer=StrokeLayer.AUTO)
        page_lineart.save_strokes(s, pid, gid, [TOP_ERASE, TOP_ERASE], layer=StrokeLayer.USER)
        s.commit()
    with make() as s:
        assert page_lineart.load_strokes(s, gid, layer=StrokeLayer.AUTO) == [TOP_LINE]
        assert page_lineart.load_strokes(s, gid, layer=StrokeLayer.USER) == [TOP_ERASE, TOP_ERASE]
        rows = {(e.generation_id, e.layer, e.page_id) for e in s.exec(select(LineartEdit)).all()}
        assert rows == {(gid, StrokeLayer.AUTO, pid), (gid, StrokeLayer.USER, pid)}
        # 改寫一層不動另一層
        page_lineart.save_strokes(s, pid, gid, [], layer=StrokeLayer.USER)
        s.commit()
        assert page_lineart.load_strokes(s, gid, layer=StrokeLayer.AUTO) == [TOP_LINE]
        assert page_lineart.load_strokes(s, gid, layer=StrokeLayer.USER) == []


def test_render_applies_auto_then_user_layer(env):
    make, storage, pid, gid = env
    with make() as s:
        base = page_lineart.ensure_page_lineart(s, storage, pid)
        assert not _ink_at(storage, base.path, 0.5, 0.08)
        page_lineart.save_strokes(s, pid, gid, [TOP_LINE], layer=StrokeLayer.AUTO)
        s.commit()
        auto = page_lineart.ensure_page_lineart(s, storage, pid)
        assert auto.key != base.key
        assert _ink_at(storage, auto.path, 0.5, 0.08) and _ink_at(storage, auto.path, 0.2, 0.08)
        # 使用者的橡皮擦在補線之後套用：擦得掉補上的線
        page_lineart.save_strokes(s, pid, gid, [TOP_ERASE], layer=StrokeLayer.USER)
        s.commit()
        both = page_lineart.ensure_page_lineart(s, storage, pid)
        assert both.key not in (base.key, auto.key)
        assert not _ink_at(storage, both.path, 0.5, 0.08) and _ink_at(storage, both.path, 0.2, 0.08)


def test_combined_strokes_json_keeps_single_layer_text(env):
    """只有一層時沿用那一層存的原文：既有頁面（只有 user 層）的 lineart_key 不變，不必重算線稿。"""
    make, storage, pid, gid = env
    user_text = page_lineart.strokes_to_json([TOP_LINE])
    auto_text = page_lineart.strokes_to_json([TOP_ERASE])
    with make() as s:
        assert page_lineart.combined_strokes_json(s, gid) == "[]"
        s.add(LineartEdit(generation_id=gid, page_id=pid, strokes_json=user_text))  # 0011 之前的資料
        s.commit()
        assert page_lineart.combined_strokes_json(s, gid) == user_text
        page = s.get(Page, pid)
        gen = s.get(Generation, gid)
        book = s.get(Book, page.book_id)
        from app.services.layout import compute_layout
        content_px = compute_layout(book.paper_size, book.print_mode).content_px
        assert page_lineart.ensure_page_lineart(s, storage, pid).key == page_lineart.lineart_key(
            gen, page, content_px, user_text)
        page_lineart.save_strokes(s, pid, gid, [TOP_ERASE], layer=StrokeLayer.AUTO)
        s.commit()
        combined = page_lineart.combined_strokes_json(s, gid)
        assert page_lineart.strokes_from_json(combined) == [TOP_ERASE, TOP_LINE]
        assert page_lineart.strokes_json_by_generation(s, [gid, gid + 99]) == {gid: combined}
        page_lineart.save_strokes(s, pid, gid, [], layer=StrokeLayer.USER)
        s.commit()
        assert page_lineart.combined_strokes_json(s, gid) == auto_text


def _set_passed(make, pid):
    with make() as s:
        p = s.get(Page, pid)
        p.status = PageStatus.PASSED
        p.passed_at = utcnow()
        s.add(p)
        s.commit()


def test_passed_page_with_new_errors_demoted(env, monkeypatch):
    make, storage, pid, gid = env
    _set_passed(make, pid)
    monkeypatch.setattr(page_lineart, "evaluate", lambda ink, w: _report(["有缺口"]))
    with make() as s:
        r = page_lineart.ensure_page_lineart(s, storage, pid)
        assert r.qa.errors
    with make() as s:
        p = s.get(Page, pid)
        assert p.status is PageStatus.DRAFT
        assert p.passed_at is None


def test_passed_page_without_errors_kept(env, monkeypatch):
    make, storage, pid, gid = env
    _set_passed(make, pid)
    monkeypatch.setattr(page_lineart, "evaluate", lambda ink, w: _report([]))
    with make() as s:
        page_lineart.ensure_page_lineart(s, storage, pid)
    with make() as s:
        p = s.get(Page, pid)
        assert p.status is PageStatus.PASSED
        assert p.passed_at is not None


def test_no_selection_raises(env):
    make, storage, pid, gid = env
    with make() as s:
        p = s.get(Page, pid)
        p.selected_generation_id = None
        s.add(p)
        s.commit()
        with pytest.raises(page_lineart.LineartUnavailable) as e:
            page_lineart.ensure_page_lineart(s, storage, pid)
        assert e.value.message == "這一頁還沒有選定圖片"


def test_missing_image_raises(env):
    make, storage, pid, gid = env
    with make() as s:
        gen = s.get(Generation, gid)
        storage.delete(gen.image_path)
        with pytest.raises(page_lineart.LineartUnavailable) as e:
            page_lineart.ensure_page_lineart(s, storage, pid)
        assert "檔案遺失" in e.value.message


def test_qa_version_in_key_forces_rerender(env, monkeypatch):
    from app.services import qa as qa_mod
    make, storage, pid, gid = env
    calls = []
    orig = page_lineart.render_base
    monkeypatch.setattr(page_lineart, "render_base",
                        lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    with make() as s:
        r1 = page_lineart.ensure_page_lineart(s, storage, pid)
        monkeypatch.setattr(qa_mod, "QA_VERSION", qa_mod.QA_VERSION + 1)
        r2 = page_lineart.ensure_page_lineart(s, storage, pid)
    assert r1.key != r2.key and len(calls) == 2


def test_lineart_path_is_keyed_and_old_file_deleted(env):
    make, storage, pid, gid = env
    with make() as s:
        r1 = page_lineart.ensure_page_lineart(s, storage, pid)
        assert r1.path.endswith(f"lineart_{r1.key}.png")
        page = s.get(Page, pid)
        page.line_width_px = 9
        s.add(page)
        s.commit()
        r2 = page_lineart.ensure_page_lineart(s, storage, pid)
    assert r2.path != r1.path
    assert storage.exists(r2.path) and not storage.exists(r1.path)


def test_lost_race_deletes_own_file(env, monkeypatch):
    make, storage, pid, gid = env
    orig = page_lineart.evaluate
    state = {"n": 0}

    def racing(ink, w):
        state["n"] += 1
        if state["n"] == 1:  # 第一次算到一半，頁面被改走
            with make() as s2:
                pg = s2.get(Page, pid)
                pg.line_width_px = 9
                s2.add(pg)
                s2.commit()
        return orig(ink, w)

    monkeypatch.setattr(page_lineart, "evaluate", racing)
    with make() as s:
        r = page_lineart.ensure_page_lineart(s, storage, pid)
        pg = s.get(Page, pid)
        assert pg.lineart_path == r.path
    page_dir = r.path.rsplit("/", 1)[0] + "/"
    files = [k for k in storage.list(page_dir) if k.rsplit("/", 1)[1].startswith("lineart_")]
    assert files == [r.path]


def test_read_page_lineart_rerenders_missing_object(env, monkeypatch):
    """資料庫說有線稿、物件卻不見了：read_page_lineart 重算並寫回同一個 key。"""
    make, storage, pid, gid = env
    calls = []
    orig = page_lineart.render_base
    monkeypatch.setattr(page_lineart, "render_base",
                        lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    with make() as s:
        lin, data = page_lineart.read_page_lineart(s, storage, pid)
        assert data == storage.get(lin.path) and len(calls) == 1
        lin2, data2 = page_lineart.read_page_lineart(s, storage, pid)
        assert (lin2.path, data2) == (lin.path, data) and len(calls) == 1
        storage.delete(lin.path)
        lin3, data3 = page_lineart.read_page_lineart(s, storage, pid)
        assert lin3.path == lin.path and data3 == data and len(calls) == 2
        assert s.get(Page, pid).lineart_path == lin.path


def test_read_page_lineart_source_missing(env):
    make, storage, pid, gid = env
    with make() as s:
        lin, _ = page_lineart.read_page_lineart(s, storage, pid)
        storage.delete(lin.path)
        storage.delete(s.get(Generation, gid).image_path)
        with pytest.raises(page_lineart.LineartSourceMissing) as e:
            page_lineart.read_page_lineart(s, storage, pid)
        assert "圖片檔案遺失" in e.value.message
