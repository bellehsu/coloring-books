"""DB ↔ storage 的身分標記（storage_instance_id）：GC 只在兩邊一致時執行（Phase 10 Task 7 修正第 3 輪）。"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.services import instance
from tests.fake_engine import FakeEngine


def _app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


def _ids(app):
    with app.state.sessions() as s:
        return instance.db_instance_id(s), instance.bucket_instance_id(app.state.storage)


def test_fresh_deployment_creates_matching_markers(settings_file):
    app = _app(settings_file)
    with TestClient(app):
        db_id, bucket_id = _ids(app)
    assert db_id and db_id == bucket_id and len(db_id) >= 32
    raw = json.loads(app.state.storage.get(instance.MARKER_KEY))
    assert raw == {"storage_instance_id": db_id}
    app2 = _app(settings_file)
    with TestClient(app2):
        assert _ids(app2) == (db_id, db_id)  # 重啟不換


def _caplog_warn(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and "instance" in r.getMessage()]


@pytest.mark.parametrize("case", ["bucket_missing", "db_missing", "differ"])
def test_startup_mismatch_warns_but_serves_and_writes_nothing(settings_file, caplog, case):
    app = _app(settings_file)
    with TestClient(app):
        db_id, _ = _ids(app)
    st = app.state.storage
    if case == "bucket_missing":
        st.delete(instance.MARKER_KEY)
    elif case == "differ":
        instance.write_bucket_instance_id(st, "f" * 32)
    else:
        with app.state.sessions() as s:
            instance.delete_db_instance_id(s)
    before = _ids(app)
    caplog.set_level(logging.WARNING)
    app2 = _app(settings_file)
    with TestClient(app2) as client:
        assert client.get("/api/books").status_code == 200
        assert _ids(app2) == before
    msgs = _caplog_warn(caplog)
    assert msgs, caplog.text
    for x in before:
        if x:
            assert x in msgs[0]


def test_bucket_marker_parse(settings_file, tmp_path):
    from app.storage import LocalStorage

    st = LocalStorage(tmp_path, b"k" * 32)
    assert instance.bucket_instance_id(st) is None
    st.put(instance.MARKER_KEY, b"not json")
    with pytest.raises(instance.InstanceMarkerError):
        instance.bucket_instance_id(st)
    st.put(instance.MARKER_KEY, json.dumps({"storage_instance_id": ""}).encode())
    with pytest.raises(instance.InstanceMarkerError):
        instance.bucket_instance_id(st)


# --- 修正第 4 輪：標記不經快取、空 DB 不認領既有 storage、競爭輸家回報真實狀態、R2 逾時與快取分 bucket ---


def test_marker_read_and_write_bypass_cache(tmp_path):
    """CachedStorage 的快取裡有舊標記（例如換 bucket 前留下的）：讀寫都直接走 inner。"""
    from app.storage import CachedStorage, LocalStorage

    inner = LocalStorage(tmp_path / "bucket", b"k" * 32)
    cache = tmp_path / "cache"
    st = CachedStorage(inner, cache, 10_000_000)
    (cache / "books").mkdir(parents=True)
    (cache / instance.MARKER_KEY).write_text(json.dumps({"storage_instance_id": "a" * 32}))
    assert st.get(instance.MARKER_KEY)  # 經過快取會讀到舊的
    assert instance.bucket_instance_id(st) is None  # inner 沒有標記
    instance.write_bucket_instance_id(st, "b" * 32)
    assert instance.bucket_instance_id(inner) == "b" * 32
    assert instance.bucket_instance_id(st) == "b" * 32
    inner.put(instance.MARKER_KEY, json.dumps({"storage_instance_id": "c" * 32}).encode())
    assert instance.bucket_instance_id(st) == "c" * 32


def _seed_existing(settings_file, *, books: int, objects: int):
    """既有部署（第 3 輪之前）：DB 已有 books 本書、storage 已有 objects 個物件，兩邊都沒有標記。"""
    from app.db import init_db, make_engine, session_factory
    from app.models import Book, Owner
    from app.storage import make_storage

    s = load_settings(settings_file)
    eng = make_engine(s.paths.database)
    init_db(eng)
    with session_factory(eng)() as sess:
        if books:
            o = Owner(name="管理員", is_admin=True, token_hash="x" * 64)
            sess.add(o)
            sess.flush()
            for _ in range(books):
                sess.add(Book(owner_id=o.id, title="書", paper_size="a4", print_mode="home"))
            sess.commit()
    eng.dispose()
    st = make_storage(s)
    for gid in range(1, objects + 1):
        st.put(st.generation_image(1, gid), b"live")
    return st


def test_upgrade_existing_db_with_books_and_objects_creates_markers(settings_file, caplog):
    """使用者的升級路徑：既有 DB 有書、storage 不是空的，第一次啟動兩邊都建立同一個標記。"""
    st = _seed_existing(settings_file, books=2, objects=5)
    caplog.set_level(logging.INFO)
    app = _app(settings_file)
    with TestClient(app):
        db_id, bucket_id = _ids(app)
    assert db_id and db_id == bucket_id
    assert instance.bucket_instance_id(st) == db_id
    assert not _caplog_warn(caplog)


def test_db_with_books_and_empty_storage_creates_markers(settings_file):
    _seed_existing(settings_file, books=1, objects=0)
    app = _app(settings_file)
    with TestClient(app):
        db_id, bucket_id = _ids(app)
    assert db_id and db_id == bucket_id


def test_empty_db_does_not_adopt_nonempty_unmarked_storage(settings_file, caplog):
    """全新（沒有書）的 DB 指到既有、沒有標記、books/ 不是空的 storage：只警告，兩邊都不建立。"""
    st = _seed_existing(settings_file, books=0, objects=15)
    caplog.set_level(logging.INFO)
    app = _app(settings_file)
    with TestClient(app) as client:
        assert client.get("/api/books").status_code == 200  # 照常服務
        assert _ids(app) == (None, None)
    assert instance.bucket_instance_id(st) is None
    msgs = _caplog_warn(caplog)
    assert msgs and "!!!" in msgs[0] and "--init-instance" in msgs[0], caplog.text
    assert len(list(st.list("books/"))) == 15  # 什麼都沒寫


def test_only_marker_in_storage_counts_as_empty(settings_file):
    """books/ 底下只有（殘留的）標記物件時算空的，但標記本身存在＝不是「兩邊都沒有」，照常只警告。"""
    from app.storage import make_storage

    st = make_storage(load_settings(settings_file))
    assert instance.storage_has_objects(st) is False
    instance.write_bucket_instance_id(st, "d" * 32)
    assert instance.storage_has_objects(st) is False
    st.put(st.generation_image(1, 1), b"x")
    assert instance.storage_has_objects(st) is True


class _RacingStorage:
    """寫入標記後，另一個行程（另一個 DB）馬上寫了不同的 id。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def put(self, key, data, content_type="image/png"):
        self._inner.put(key, data, content_type)
        if key == instance.MARKER_KEY:
            self._inner.put(key, json.dumps({"storage_instance_id": "e" * 32}).encode(), content_type)


def test_race_loser_reports_real_bucket_state(settings_file, caplog):
    from app.db import init_db, make_engine, session_factory
    from app.storage import make_storage

    s = load_settings(settings_file)
    eng = make_engine(s.paths.database)
    init_db(eng)
    st = _RacingStorage(make_storage(s))
    with session_factory(eng)() as sess:
        res = instance.ensure_instance(sess, st)
    assert res.bucket_id == "e" * 32 and res.db_id != res.bucket_id
    assert not res.ok


def test_ensure_does_not_overwrite_marker_written_meanwhile(settings_file, monkeypatch):
    """兩邊都沒有→準備建立，但寫之前 bucket 已經有別人的標記：不覆寫，回報對不上。"""
    from app.db import init_db, make_engine, session_factory
    from app.storage import make_storage

    s = load_settings(settings_file)
    eng = make_engine(s.paths.database)
    init_db(eng)
    st = make_storage(s)
    real = instance.storage_has_objects

    def has_objects(storage):  # 檢查完之後、寫入之前，別的行程寫了標記
        out = real(storage)
        instance.write_bucket_instance_id(st, "f" * 32)
        return out

    monkeypatch.setattr(instance, "storage_has_objects", has_objects)
    with session_factory(eng)() as sess:
        res = instance.ensure_instance(sess, st)
    assert instance.bucket_instance_id(st) == "f" * 32
    assert res.bucket_id == "f" * 32 and not res.ok


def test_marker_read_error_is_wrapped(tmp_path):
    from app.storage import LocalStorage

    class Broken(LocalStorage):
        def get(self, key):
            raise TimeoutError("read timed out")

    with pytest.raises(instance.InstanceUnavailable):
        instance.bucket_instance_id(Broken(tmp_path, b"k" * 32))


@pytest.fixture
def r2_env(monkeypatch, tmp_path):
    import moto

    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    for k, v in {"BACKEND": "r2", "R2__ENDPOINT": "https://s3.amazonaws.com", "R2__BUCKET": "bucket-a",
                 "R2__ACCESS_KEY": "AK", "R2__SECRET_KEY": "SK", "CACHE_DIR": str(tmp_path / "r2cache")}.items():
        monkeypatch.setenv(f"APP_STORAGE__{k}", v)
    with moto.mock_aws():
        yield tmp_path / "r2cache"


def test_r2_client_has_short_timeouts(settings_file, r2_env):
    """R2 連不上時不能卡住啟動好幾分鐘：所有 storage 用的 boto3 client 都設了逾時與重試次數。"""
    from app.storage import make_storage

    cfg = make_storage(load_settings(settings_file)).inner._client.meta.config
    assert cfg.connect_timeout == 5 and cfg.read_timeout == 30
    assert cfg.retries["total_max_attempts"] == 4  # max_attempts=3 次重試＋第一次
    assert cfg.signature_version == "s3v4"


def test_r2_cache_dir_is_namespaced_by_bucket(settings_file, r2_env, monkeypatch):
    from app.storage import make_storage

    a = make_storage(load_settings(settings_file))
    monkeypatch.setenv("APP_STORAGE__R2__BUCKET", "bucket-b")
    b = make_storage(load_settings(settings_file))
    assert a.cache_dir == r2_env / "bucket-a" and b.cache_dir == r2_env / "bucket-b"
