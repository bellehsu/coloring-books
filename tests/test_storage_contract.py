"""Storage 合約測試：LocalStorage、R2Storage（moto）、CachedStorage(LocalStorage) 行為一致。

R2Storage 一律在 moto.mock_aws 下跑；endpoint 用 moto 會攔截的 AWS 網址，不連真正的網路。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from app.storage import (  # noqa: E402
    CachedStorage,
    LocalStorage,
    R2Storage,
    StorageNotFound,
)

BUCKET = "test-bucket"
# moto 只攔截 AWS 網域；R2 的 endpoint 在測試裡換成這個，避免真的連線
MOTO_ENDPOINT = "https://s3.amazonaws.com"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """結構性擋掉真正的網路：socket connect 與 DNS 一律丟錯（moto 在 botocore 層攔截，不需要 socket）。"""
    import socket

    def blocked(*args, **kwargs):
        raise RuntimeError(f"測試不可連網：{args!r}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "gethostbyname", blocked)


def test_network_is_blocked():
    import socket

    with pytest.raises(RuntimeError):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 9))


@pytest.fixture
def aws_env(monkeypatch):
    """確保不會讀到本機真正的 AWS 憑證。"""
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def r2(aws_env):
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield R2Storage(MOTO_ENDPOINT, BUCKET, "AKTEST", "SKTEST-very-secret")


@pytest.fixture(params=["local", "r2", "cached"])
def storage(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path / "root", b"k" * 32)
    if request.param == "r2":
        return request.getfixturevalue("r2")
    return CachedStorage(LocalStorage(tmp_path / "root", b"k" * 32), tmp_path / "cache", 10_000)


def test_put_get_roundtrip(storage):
    storage.put("books/1/generations/2.png", b"png-bytes")
    assert storage.get("books/1/generations/2.png") == b"png-bytes"
    storage.put("books/1/generations/2.png", b"new", "image/png")
    assert storage.get("books/1/generations/2.png") == b"new"


def test_get_missing_raises_not_found(storage):
    with pytest.raises(StorageNotFound):
        storage.get("books/1/generations/404.png")
    # StorageNotFound 是 KeyError，舊的 except KeyError 也接得住
    assert issubclass(StorageNotFound, KeyError)


def test_delete_missing_is_noop(storage):
    storage.delete("books/1/generations/404.png")
    storage.put("books/1/a.png", b"x")
    storage.delete("books/1/a.png")
    storage.delete("books/1/a.png")
    with pytest.raises(StorageNotFound):
        storage.get("books/1/a.png")


def test_exists(storage):
    assert not storage.exists("books/1/a.png")
    storage.put("books/1/a.png", b"x")
    assert storage.exists("books/1/a.png")
    storage.delete("books/1/a.png")
    assert not storage.exists("books/1/a.png")


def test_list_prefix_returns_only_matching(storage):
    keys = ["books/1/generations/1.png", "books/1/generations/1_mask.png",
            "books/1/pages/3/lineart_ab.png", "books/12/generations/9.png", "cache/previews/1_x.png"]
    for k in keys:
        storage.put(k, b"x")
    assert sorted(storage.list("books/1/")) == sorted(keys[:3])
    assert sorted(storage.list("books/1/generations/")) == keys[:2]
    assert list(storage.list("books/99/")) == []
    assert sorted([*storage.list("books/"), *storage.list("cache/")]) == sorted(keys)


def test_delete_prefix(storage):
    keys = ["books/1/pages/3/lineart_a.png", "books/1/pages/3/lineart_b.png",
            "books/1/pages/4/lineart_a.png", "books/2/pages/3/lineart_a.png"]
    for k in keys:
        storage.put(k, b"x")
    storage.delete_prefix("books/1/pages/3/")
    assert sorted(storage.list("books/")) == sorted(keys[2:])
    for k in keys[:2]:
        assert not storage.exists(k)
        with pytest.raises(StorageNotFound):
            storage.get(k)
    storage.delete_prefix("books/77/")  # 不存在的 prefix 不報錯


@pytest.mark.parametrize("bad_prefix", ["", "books/1", "books", "app", "books/1/a.png"])
def test_delete_prefix_requires_trailing_slash(storage, bad_prefix):
    keys = ["books/1/a.png", "books/10/a.png", "books/12/a.png"]
    for k in keys:
        storage.put(k, b"x")
    with pytest.raises(ValueError):
        storage.delete_prefix(bad_prefix)
    for k in keys:
        assert storage.exists(k)
    # books/1/ 只刪 books/1，不碰 books/10、books/12
    storage.delete_prefix("books/1/")
    assert sorted(storage.list("books/")) == ["books/10/a.png", "books/12/a.png"]


@pytest.mark.parametrize("make", ["local", "cached"])
def test_local_prefix_ops_limited_to_books_and_cache(tmp_path, make):
    root = tmp_path / "root"
    local = LocalStorage(root, b"k")
    st = local if make == "local" else CachedStorage(local, tmp_path / "cache", 10_000)
    root.mkdir(parents=True)
    (root / "app.db").write_bytes(b"db")
    (root / ".signing_key").write_bytes(b"k" * 64)
    st.put("books/1/a.png", b"x")
    st.put("cache/previews/1_a.png", b"x")
    for bad in ("", "app", "a", ".", ".s", "storage/", "booksx/", "books"):
        with pytest.raises(ValueError):
            list(st.list(bad))
    for bad in ("app/", ".signing_key/", "other/"):
        with pytest.raises(ValueError):
            st.delete_prefix(bad)
    assert list(st.list("books/")) == ["books/1/a.png"]
    assert list(st.list("cache/")) == ["cache/previews/1_a.png"]
    assert list(st.list("books/1")) == ["books/1/a.png"]
    st.delete_prefix("cache/")
    st.delete_prefix("books/")
    assert (root / "app.db").read_bytes() == b"db"
    assert (root / ".signing_key").exists()


BAD_KEYS = ["../evil.png", "books/../../evil.png", "books/1/..", "/etc/passwd", "/books/1/a.png"]


@pytest.mark.parametrize("bad", BAD_KEYS)
def test_bad_keys_rejected_everywhere(storage, bad, tmp_path):
    calls = [
        lambda: storage.put(bad, b"x"),
        lambda: storage.get(bad),
        lambda: storage.delete(bad),
        lambda: storage.exists(bad),
        lambda: storage.signed_url(bad, 60),
        lambda: list(storage.list(bad)),
        lambda: storage.delete_prefix(bad),
    ]
    for call in calls:
        with pytest.raises(ValueError):
            call()
    assert not (tmp_path / "evil.png").exists()


def test_key_builders_unchanged(storage):
    assert storage.generation_image(3, 7) == "books/3/generations/7.png"
    assert storage.generation_mask(3, 7) == "books/3/generations/7_mask.png"
    assert storage.lineart(3, 5, "ab") == "books/3/pages/5/lineart_ab.png"


# ---------- LocalStorage 簽章 ----------


def test_local_signed_url_verifies(tmp_path):
    st = LocalStorage(tmp_path, b"secret-signing-key")
    st.put("books/1/generations/2.png", b"x")
    now = 1_800_000_000
    url = st.signed_url("books/1/generations/2.png", 120, now=now)
    u = urlparse(url)
    assert u.path == "/api/files/books/1/generations/2.png"
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    exp, sig = int(q["exp"]), q["sig"]
    assert exp == now + 120
    assert st.verify("books/1/generations/2.png", exp, sig, now=now)
    assert st.verify("books/1/generations/2.png", str(exp), sig, now=now + 119)
    # 竄改 key／sig／exp
    assert not st.verify("books/1/generations/3.png", exp, sig, now=now)
    assert not st.verify("books/1/generations/2.png", exp, sig[:-1] + ("0" if sig[-1] != "0" else "1"), now=now)
    assert not st.verify("books/1/generations/2.png", exp + 1, sig, now=now)
    assert not st.verify("books/1/generations/2.png", "abc", sig, now=now)
    assert not st.verify("../evil.png", exp, sig, now=now)
    # 非 ASCII／非字串的 sig 回 False，不丟 TypeError
    assert not st.verify("books/1/generations/2.png", exp, "é" * 64, now=now)
    assert not st.verify("books/1/a.png", 9999999999, "é" * 64)
    assert not st.verify("books/1/generations/2.png", exp, None, now=now)
    assert not st.verify("books/1/generations/2.png", exp, sig.upper() + "\x00", now=now)
    # 過期
    assert not st.verify("books/1/generations/2.png", exp, sig, now=now + 121)
    # 不同金鑰簽的不算
    other = LocalStorage(tmp_path, b"other-key")
    assert not other.verify("books/1/generations/2.png", exp, sig, now=now)


def test_local_signed_url_uses_current_time(tmp_path):
    st = LocalStorage(tmp_path, b"k")
    q = parse_qs(urlparse(st.signed_url("books/1/a.png", 120)).query)
    assert abs(int(q["exp"][0]) - (time.time() + 120)) < 5
    assert st.verify("books/1/a.png", q["exp"][0], q["sig"][0])


# ---------- R2Storage ----------


def test_r2_presigned_url_contains_bucket_and_expires(r2):
    r2.put("books/1/generations/2.png", b"x")
    u = urlparse(r2.signed_url("books/1/generations/2.png", 120))
    assert BUCKET in u.netloc + u.path
    assert u.path.endswith("/books/1/generations/2.png")
    q = parse_qs(u.query)
    assert q["X-Amz-Expires"] == ["120"]
    assert "X-Amz-Signature" in q


def test_r2_put_sets_content_type(r2):
    r2.put("books/1/a.png", b"x", "image/png")
    head = r2._client.head_object(Bucket=BUCKET, Key="books/1/a.png")
    assert head["ContentType"] == "image/png"


def test_r2_repr_hides_secret(r2):
    assert "SKTEST-very-secret" not in repr(r2)
    assert "SKTEST-very-secret" not in str(r2)


def test_r2_client_config(aws_env):
    with moto.mock_aws():
        st = R2Storage(MOTO_ENDPOINT, BUCKET, "AK", "SK")
        assert st._client.meta.region_name == "auto"
        assert st._client.meta.config.signature_version == "s3v4"
        assert st._client.meta.endpoint_url == MOTO_ENDPOINT


# ---------- CachedStorage ----------


class CountingStorage(LocalStorage):
    def __init__(self, root: Path):
        super().__init__(root, b"k")
        self.gets = 0

    def get(self, key: str) -> bytes:
        self.gets += 1
        return super().get(key)


def _cache_files(cache_dir: Path) -> list[str]:
    return sorted(p.relative_to(cache_dir).as_posix() for p in cache_dir.rglob("*") if p.is_file())


def test_cache_reads_through_once(tmp_path):
    inner = CountingStorage(tmp_path / "root")
    inner.put("books/1/a.png", b"abc")
    st = CachedStorage(inner, tmp_path / "cache", 10_000)
    assert st.get("books/1/a.png") == b"abc"
    assert st.get("books/1/a.png") == b"abc"
    assert inner.gets == 1
    assert _cache_files(tmp_path / "cache") == ["books/1/a.png"]


def test_cache_put_and_delete_update_cache(tmp_path):
    inner = CountingStorage(tmp_path / "root")
    st = CachedStorage(inner, tmp_path / "cache", 10_000)
    st.put("books/1/a.png", b"v1")
    assert st.get("books/1/a.png") == b"v1"
    st.put("books/1/a.png", b"v2")
    assert st.get("books/1/a.png") == b"v2"
    st.delete("books/1/a.png")
    with pytest.raises(StorageNotFound):
        st.get("books/1/a.png")
    assert _cache_files(tmp_path / "cache") == []


def test_cache_delete_prefix_invalidates(tmp_path):
    st = CachedStorage(LocalStorage(tmp_path / "root", b"k"), tmp_path / "cache", 10_000)
    st.put("books/1/pages/3/lineart_a.png", b"x")
    st.put("books/1/pages/4/lineart_a.png", b"y")
    st.get("books/1/pages/3/lineart_a.png")
    st.delete_prefix("books/1/pages/3/")
    assert _cache_files(tmp_path / "cache") == ["books/1/pages/4/lineart_a.png"]


def test_cache_survives_cache_dir_removal(tmp_path):
    import shutil

    st = CachedStorage(LocalStorage(tmp_path / "root", b"k"), tmp_path / "cache", 10_000)
    st.put("books/1/a.png", b"x")
    shutil.rmtree(tmp_path / "cache")
    assert st.get("books/1/a.png") == b"x"


def test_cache_evicts_oldest_over_max(tmp_path):
    inner = LocalStorage(tmp_path / "root", b"k")
    for i in range(4):
        inner.put(f"books/1/{i}.png", bytes(100))
    cache = tmp_path / "cache"
    st = CachedStorage(inner, cache, 300)
    base = time.time() - 1000
    for i in range(3):
        st.get(f"books/1/{i}.png")
        os.utime(cache / f"books/1/{i}.png", (base + i, base + i))
    # 讀到 1 → 變成最近使用，最舊的是 0
    st.get("books/1/1.png")
    st.get("books/1/3.png")  # 總量 400 > 300，淘汰最舊的 0
    assert _cache_files(cache) == ["books/1/1.png", "books/1/2.png", "books/1/3.png"]
    assert sum((cache / f).stat().st_size for f in _cache_files(cache)) <= 300


def test_cache_skips_objects_larger_than_max(tmp_path):
    inner = LocalStorage(tmp_path / "root", b"k")
    inner.put("books/1/small.png", bytes(10))
    inner.put("books/1/big.png", bytes(500))
    st = CachedStorage(inner, tmp_path / "cache", 100)
    st.get("books/1/small.png")
    assert st.get("books/1/big.png") == bytes(500)
    assert _cache_files(tmp_path / "cache") == ["books/1/small.png"]


def test_cache_atomic_write(tmp_path, monkeypatch):
    import app.storage as storage_mod

    inner = LocalStorage(tmp_path / "root", b"k")
    inner.put("books/1/a.png", b"abcdef" * 1000)
    cache = tmp_path / "cache"
    st = CachedStorage(inner, cache, 1_000_000)

    real_replace = os.replace

    def failing_replace(src, dst):
        if str(dst).startswith(str(cache)):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(storage_mod.os, "replace", failing_replace)
    # 快取寫入失敗不影響讀取，也不留下半個檔案或暫存檔
    assert st.get("books/1/a.png") == b"abcdef" * 1000
    assert _cache_files(cache) == []
    monkeypatch.setattr(storage_mod.os, "replace", real_replace)
    assert st.get("books/1/a.png") == b"abcdef" * 1000
    assert _cache_files(cache) == ["books/1/a.png"]


def test_cache_put_failure_does_not_leave_stale_entry(tmp_path, monkeypatch):
    import app.storage as storage_mod

    cache = tmp_path / "cache"
    st = CachedStorage(LocalStorage(tmp_path / "root", b"k"), cache, 1_000_000)
    st.put("books/1/a.png", b"old")
    st.get("books/1/a.png")
    real_replace = os.replace

    def failing_replace(src, dst):
        if str(dst).startswith(str(cache)):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(storage_mod.os, "replace", failing_replace)
    st.put("books/1/a.png", b"new")
    monkeypatch.setattr(storage_mod.os, "replace", real_replace)
    assert st.get("books/1/a.png") == b"new"


def test_local_put_is_atomic(tmp_path, monkeypatch):
    import app.storage as storage_mod

    st = LocalStorage(tmp_path / "root", b"k")
    st.put("books/1/a.png", b"old")

    seen = []

    def boom(src, dst):
        seen.append(Path(src).name)
        raise OSError("disk full")

    monkeypatch.setattr(storage_mod.os, "replace", boom)
    with pytest.raises(OSError):
        st.put("books/1/a.png", b"new")
    monkeypatch.undo()
    # 暫存檔以 .tmp 結尾（既有測試用 rglob("*.tmp") 檢查沒有殘留）
    assert seen and seen[0].endswith(".tmp")
    assert st.get("books/1/a.png") == b"old"
    assert sorted(st.list("books/")) == ["books/1/a.png"]


def test_local_storage_rejects_keys_outside_books_and_cache(tmp_path):
    from app.storage import LocalStorage

    st = LocalStorage(tmp_path, b"k")
    (tmp_path / "app.db").write_bytes(b"db")
    for key in ("app.db", ".signing_key", "other/x.png"):
        for call in (lambda: st.get(key), lambda: st.put(key, b"x"), lambda: st.exists(key),
                     lambda: st.delete(key), lambda: st.signed_url(key, 60)):
            with pytest.raises(ValueError):
                call()
        assert st.verify(key, 9999999999, "0" * 64) is False
    assert (tmp_path / "app.db").read_bytes() == b"db"
    st.put("books/1/a.png", b"x")
    st.put("cache/previews/a.png", b"x")
    assert st.exists("books/1/a.png") and st.exists("cache/previews/a.png")
