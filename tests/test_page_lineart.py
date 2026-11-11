import io

import pytest
from PIL import Image, ImageDraw

from app.db import init_db, make_engine, session_factory
from app.models import (Book, GenKind, Generation, GenStatus, Owner, Page, PageStatus, utcnow)
from app.services import page_lineart
from app.services.layout import PaperSize, PrintMode
from app.services.lineart_render import Stroke
from app.services.qa import QAReport
from app.storage import LocalStorage


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
            s, pid, gid, [Stroke("draw", 12.0, ((0.1, 0.1), (0.9, 0.9)))]
        )
        s.commit()
        assert len(page_lineart.load_strokes(s, gid)) == 1
        r2 = page_lineart.ensure_page_lineart(s, storage, pid)
        assert r2.key != r1.key
        assert storage.get(r2.path) != before


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


def test_render_preview_cached(env, monkeypatch):
    make, storage, pid, gid = env
    calls = []
    orig = page_lineart.render_base
    monkeypatch.setattr(page_lineart, "render_base",
                        lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    with make() as s:
        gen = s.get(Generation, gid)
        data = page_lineart.render_preview(storage, gen, (300, 400), "standard", 11)
        assert data.startswith(b"\x89PNG")
        keys = list(storage.list(f"cache/previews/{gid}_"))
        assert len(keys) == 1 and storage.get(keys[0]) == data
        assert page_lineart.render_preview(storage, gen, (300, 400), "standard", 11) == data
        assert len(calls) == 1  # 第二次直接讀快取的物件
        storage.delete(keys[0])  # 物件不見了（GC 或手動刪除）→ 重算
        assert page_lineart.render_preview(storage, gen, (300, 400), "standard", 11) == data
        assert len(calls) == 2


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
