"""檔案儲存：所有檔案以物件 key（相對路徑，例如 books/1/generations/2.png）記錄在資料庫。

Storage 介面有兩個實作：LocalStorage（資料夾）與 R2Storage（Cloudflare R2，S3 API），
R2 外面再包 CachedStorage（本機磁碟快取）。make_storage(settings) 依設定挑選。
本檔後半是上傳圖片的驗證與轉檔工具。
"""

from __future__ import annotations

import hashlib
import hmac
import io
import logging
import os
import re
import secrets
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import SettingsError
from .services.imageio import flatten_on_white

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger(__name__)

MIN_SIDE = 64
# 上傳背景：解碼前先擋像素數（避免小檔案、超大尺寸的圖把記憶體吃光）；48MP 手機照片仍可通過
UPLOAD_MAX_PIXELS = 64_000_000
UPLOAD_TOO_MANY_PIXELS = "圖片像素太多（最多 6400 萬像素）"


class ImageRejected(ValueError):
    """上傳圖片不合格（格式、大小或尺寸）。訊息說明原因。"""


class TooManyPixels(ImageRejected):
    """像素數超過上限（解碼前就判斷）。API 回 422。"""


class StorageNotFound(KeyError):
    """物件不存在（get 時）。繼承 KeyError，訊息是 key。"""


def _check_key(key: str, *, prefix: bool = False) -> str:
    """驗證物件 key：相對路徑、以 / 分隔、不可有 ..、.、空段落、反斜線或 NUL。
    prefix=True 時允許空字串與結尾的 /（list／delete_prefix 用）。不合格丟 ValueError。"""
    if not isinstance(key, str):
        raise ValueError(f"key 必須是字串：{key!r}")
    if key == "":
        if prefix:
            return key
        raise ValueError("key 不可為空")
    if key.startswith("/") or "\\" in key or "\x00" in key:
        raise ValueError(f"不合法的 key：{key!r}")
    parts = key.split("/")
    if prefix and parts[-1] == "":
        parts = parts[:-1]
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"不合法的 key：{key!r}")
    return key


def _check_delete_prefix(prefix: str) -> str:
    """delete_prefix 的 prefix 必須非空且以 / 結尾（避免 books/1 誤刪 books/10、app 誤刪 app.db）。"""
    _check_key(prefix, prefix=True)
    if not prefix or not prefix.endswith("/"):
        raise ValueError(f"delete_prefix 的 prefix 必須非空且以 / 結尾：{prefix!r}")
    return prefix


# LocalStorage 的 root 也放 app.db 與 .signing_key；list／delete_prefix 只能碰這兩個資料夾
_LOCAL_PREFIX_ROOTS = ("books/", "cache/")


def _check_local_prefix(prefix: str) -> str:
    if not prefix.startswith(_LOCAL_PREFIX_ROOTS):
        raise ValueError(f"LocalStorage 只能列出／刪除 books/ 或 cache/ 底下：{prefix!r}")
    return prefix


def _check_local_key(key: str) -> str:
    """LocalStorage 的每個讀、寫、簽章都只能碰 books/ 或 cache/ 底下（root 還有 app.db 與 .signing_key）。"""
    _check_key(key)
    if not key.startswith(_LOCAL_PREFIX_ROOTS):
        raise ValueError(f"LocalStorage 只能存取 books/ 或 cache/ 底下：{key!r}")
    return key


class Storage(Protocol):
    """所有二進位檔案（生成圖、遮罩、線稿、預覽）的存取介面。key 是今天的相對路徑格式。"""

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None: ...
    def get(self, key: str) -> bytes: ...  # 不存在 → StorageNotFound
    def delete(self, key: str) -> None: ...  # 不存在 → 不報錯
    def exists(self, key: str) -> bool: ...
    def signed_url(self, key: str, ttl_s: int) -> str: ...
    def list(self, prefix: str) -> Iterator[str]: ...
    def delete_prefix(self, prefix: str) -> None: ...  # prefix 必須非空且以 / 結尾

    def generation_image(self, book_id: int, gen_id: int) -> str: ...
    def generation_mask(self, book_id: int, gen_id: int) -> str: ...
    def lineart(self, book_id: int, page_id: int, key: str) -> str: ...


class _KeyBuilders:
    """物件 key 的格式（與資料庫裡既有的相對路徑相同，不可改）。"""

    def generation_image(self, book_id: int, gen_id: int) -> str:
        return f"books/{book_id}/generations/{gen_id}.png"

    def generation_mask(self, book_id: int, gen_id: int) -> str:
        return f"books/{book_id}/generations/{gen_id}_mask.png"

    def lineart(self, book_id: int, page_id: int, key: str) -> str:
        return f"books/{book_id}/pages/{page_id}/lineart_{key}.png"


_TMP_PREFIX = ".tmp-"


def _atomic_write(path: Path, data: bytes) -> None:
    """先寫同資料夾的暫存檔再 rename；失敗時刪掉暫存檔，不留下寫一半的檔案。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=_TMP_PREFIX + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _walk_files(root: Path, prefix: str) -> Iterator[tuple[str, Path]]:
    """列出 root 底下 key 以 prefix 開頭的檔案（略過寫入中的暫存檔）。回傳 (key, 絕對路徑)。"""
    # 從 prefix 最深的完整資料夾開始走，不用掃整個 root
    base_rel = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
    base = root / base_rel if base_rel else root
    if not base.is_dir():
        return
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            if name.startswith(_TMP_PREFIX):
                continue
            full = Path(dirpath) / name
            key = full.relative_to(root).as_posix()
            if key.startswith(prefix):
                yield key, full


def _prune_empty_dirs(root: Path, prefix: str) -> None:
    """delete_prefix 後清掉 prefix 所在資料夾底下（含）的空資料夾，不碰 root 本身。"""
    base_rel = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
    if not base_rel:
        return
    base = root / base_rel
    if not base.is_dir():
        return
    for dirpath, _dirs, _files in os.walk(base, topdown=False):
        try:
            Path(dirpath).rmdir()
        except OSError:
            pass  # 不是空的


class LocalStorage(_KeyBuilders):
    """以資料夾為後端。signed_url 指向後端 /api/files/{key}，以 HMAC 簽章。這是 `storage.backend: local` 的正式後端（設定範本的預設值），
    不是只給測試用的假後端：檢查與錯誤處理要跟 R2 一樣小心。"""

    def __init__(self, root: Path, signing_key: bytes):
        """signing_key 必填：同一個 storage 的每個實例（多個行程、重啟後）都要用同一把，簽出的網址才驗得過。"""
        if not isinstance(signing_key, bytes) or not signing_key:
            raise ValueError("LocalStorage 需要非空的 signing_key（bytes）")
        self.root = Path(root)
        self._signing_key = signing_key

    def __repr__(self) -> str:
        return f"LocalStorage(root={str(self.root)!r})"

    def _path(self, key: str) -> Path:
        _check_local_key(key)
        root = self.root.resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError(f"路徑超出儲存根目錄：{key!r}")
        return path

    # --- Storage 介面 ---

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        _atomic_write(self._path(key), data)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise StorageNotFound(key) from None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> Iterator[str]:
        _check_local_prefix(_check_key(prefix, prefix=True))
        return (key for key, _ in _walk_files(self.root, prefix))

    def delete_prefix(self, prefix: str) -> None:
        _check_local_prefix(_check_delete_prefix(prefix))
        for _key, path in list(_walk_files(self.root, prefix)):
            path.unlink(missing_ok=True)
        _prune_empty_dirs(self.root, prefix)

    def _sign(self, key: str, exp: int) -> str:
        return hmac.new(self._signing_key, f"{key}\n{exp}".encode(), hashlib.sha256).hexdigest()

    def signed_url(self, key: str, ttl_s: int, *, now: float | None = None) -> str:
        _check_local_key(key)
        exp = int(time.time() if now is None else now) + int(ttl_s)
        return f"/api/files/{quote(key, safe='/')}?exp={exp}&sig={self._sign(key, exp)}"

    def verify(self, key: str, exp: int | str, sig: str, now: float | None = None) -> bool:
        """檢查 /api/files 的簽章：key 合法、未過期、HMAC 相符。任何不合格都回 False。"""
        try:
            _check_local_key(key)
            exp_i = int(exp)
        except (ValueError, TypeError):
            return False
        if exp_i < (time.time() if now is None else now):
            return False
        # compare_digest 遇到非 ASCII 的 str 會丟 TypeError；不是 ASCII 字串就直接不合格
        if not isinstance(sig, str) or not sig.isascii():
            return False
        return hmac.compare_digest(self._sign(key, exp_i).encode(), sig.encode())


# boto3 client 的逾時（秒）與重試次數（不含第一次）：R2 被擋時每個請求最多約 4 × 5 秒就放棄
R2_CONNECT_TIMEOUT_S = 5
R2_READ_TIMEOUT_S = 30
R2_MAX_RETRIES = 3


class R2Storage(_KeyBuilders):
    """Cloudflare R2（S3 API）。signed_url 是 S3 presigned GET。憑證只交給 boto3 client，不存在屬性裡。"""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, client: Any = None):
        self.endpoint = endpoint
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name="auto",
                # R2 連不上時不能卡住（啟動的身分標記檢查、API 請求）：預設逾時 60 秒＋重試會卡好幾分鐘
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=R2_CONNECT_TIMEOUT_S,
                    read_timeout=R2_READ_TIMEOUT_S,
                    retries={"max_attempts": R2_MAX_RETRIES},
                ),
            )
        self._client = client

    def __repr__(self) -> str:
        return f"R2Storage(endpoint={self.endpoint!r}, bucket={self.bucket!r})"

    @staticmethod
    def _is_not_found(e: Exception) -> bool:
        code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
        return code in ("NoSuchKey", "404", "NotFound")

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        _check_key(key)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        _check_key(key)
        from botocore.exceptions import ClientError

        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if self._is_not_found(e):
                raise StorageNotFound(key) from None
            raise
        return resp["Body"].read()

    def delete(self, key: str) -> None:
        _check_key(key)
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        _check_key(key)
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if self._is_not_found(e):
                return False
            raise
        return True

    def signed_url(self, key: str, ttl_s: int) -> str:
        _check_key(key)
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=int(ttl_s)
        )

    def list(self, prefix: str) -> Iterator[str]:
        _check_key(prefix, prefix=True)
        return self._iter_keys(prefix)

    def _iter_keys(self, prefix: str) -> Iterator[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def delete_prefix(self, prefix: str) -> None:
        _check_delete_prefix(prefix)
        batch: list[dict[str, str]] = []
        for key in self._iter_keys(prefix):
            batch.append({"Key": key})
            if len(batch) == 1000:  # S3 DeleteObjects 一次最多 1000 筆
                self._delete_batch(batch)
                batch = []
        if batch:
            self._delete_batch(batch)

    def _delete_batch(self, batch: list[dict[str, str]]) -> None:
        resp = self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
        errors = resp.get("Errors") or []
        if errors:
            raise OSError(f"R2 刪除失敗 {len(errors)} 筆，例如 {errors[0].get('Key')!r}：{errors[0].get('Code')}")


CACHE_LOW_WATER = 0.9  # 快取超過上限時刪到上限的這個比例


class CachedStorage(_KeyBuilders):
    """包住遠端 Storage，get 經過本機磁碟快取（依 mtime 淘汰最舊的，總量上限 max_bytes）。

    快取只是加速：整個資料夾隨時可刪；快取寫入失敗只記 log，不影響讀寫結果。"""

    def __init__(self, inner: Storage, cache_dir: Path, max_bytes: int):
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.max_bytes = int(max_bytes)
        # 快取總量的估計（只加不減：覆寫、刪除都不扣，寧可高估）。None＝還沒掃過。
        # 只有估計超過上限才真的掃資料夾、依 mtime 淘汰，掃完換成實際總量；不必每次寫入都掃。
        # 估計值只是這個行程的（多個執行緒同時加可能少算一點；別的行程寫入的不算），
        # 所以快取最多可能超出上限約「上限 ×（共用快取的行程數）」，下一次掃描就會修正。
        self._approx_total: int | None = None

    def __repr__(self) -> str:
        return f"CachedStorage(inner={self.inner!r}, cache_dir={str(self.cache_dir)!r})"

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / _check_key(key)

    def _store(self, key: str, data: bytes) -> None:
        path = self._cache_path(key)
        if len(data) > self.max_bytes:
            path.unlink(missing_ok=True)
            return
        try:
            _atomic_write(path, data)
        except OSError:
            log.warning("快取寫入失敗：%s", key, exc_info=True)
            path.unlink(missing_ok=True)  # 不留下舊版本
            return
        if self._approx_total is not None:
            self._approx_total += len(data)
            if self._approx_total <= self.max_bytes:
                return
        self._evict()

    def _evict(self) -> None:
        """總量超過上限時，從 mtime 最舊的開始刪；記下刪完後的實際總量。"""
        entries: list[tuple[int, str, Path, int]] = []
        total = 0
        for _key, path in _walk_files(self.cache_dir, ""):
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            entries.append((st.st_mtime_ns, str(path), path, st.st_size))
            total += st.st_size
        if total > self.max_bytes:
            # 一次刪到低水位（上限的 90%），留一段空間：快取滿了之後不會每寫一次就再掃一次
            target = self.max_bytes * CACHE_LOW_WATER
            entries.sort()
            for _mtime, _name, path, size in entries:
                if total <= target:
                    break
                path.unlink(missing_ok=True)
                total -= size
        self._approx_total = total

    def get(self, key: str) -> bytes:
        path = self._cache_path(key)
        try:
            data = path.read_bytes()
        except OSError:
            data = None
        if data is not None:
            try:
                os.utime(path)  # 標記為最近使用
            except OSError:
                pass
            return data
        data = self.inner.get(key)
        self._store(key, data)
        return data

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        path = self._cache_path(key)
        path.unlink(missing_ok=True)  # 先作廢，遠端失敗時也不會讀到舊快取
        self.inner.put(key, data, content_type)
        self._store(key, data)

    def delete(self, key: str) -> None:
        self._cache_path(key).unlink(missing_ok=True)
        self.inner.delete(key)

    def exists(self, key: str) -> bool:
        _check_key(key)
        return self.inner.exists(key)

    def signed_url(self, key: str, ttl_s: int) -> str:
        _check_key(key)
        return self.inner.signed_url(key, ttl_s)

    def list(self, prefix: str) -> Iterator[str]:
        _check_key(prefix, prefix=True)
        return self.inner.list(prefix)

    def delete_prefix(self, prefix: str) -> None:
        _check_delete_prefix(prefix)
        self.inner.delete_prefix(prefix)
        for _key, path in list(_walk_files(self.cache_dir, prefix)):
            path.unlink(missing_ok=True)


SIGNING_KEY_FILE = ".signing_key"


def _load_or_create_signing_key(storage_dir: Path) -> bytes:
    return load_or_create_key_file(storage_dir, SIGNING_KEY_FILE, "簽章金鑰")


def load_or_create_key_file(storage_dir: Path, filename: str, label: str) -> bytes:
    """讀取 <storage_dir>/<filename>；不存在就產生一把（0600）。以 link 原子建立，兩個行程同時啟動也只會有一把。
    簽章金鑰（.signing_key）與邀請連結金鑰（.link_key，services/auth.py）共用。"""
    path = Path(storage_dir) / filename
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=_TMP_PREFIX + filename + ".", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(secrets.token_hex(32))
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass  # 別的行程先建好了，用它的
        finally:
            Path(tmp).unlink(missing_ok=True)
    # 既有的金鑰檔權限比 0600 寬就收緊（例如手動建立或從備份還原）
    if os.stat(path).st_mode & 0o077:
        os.chmod(path, 0o600)
    key = path.read_bytes().strip()
    if len(key) < 32:
        raise SettingsError([f"{label}檔內容不完整：{path}（刪除後重啟即可重新產生）"])
    return key


# S3／R2 的 bucket 命名規則（同時保證它是一段安全的路徑：沒有 /、反斜線、..，也不是 . 或 ..）
_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")


def make_storage(settings: "Settings") -> Storage:
    """依 storage.backend 建立 Storage：local → LocalStorage；r2 → CachedStorage(R2Storage)，快取在 <cache_dir>/<bucket>。"""
    st = settings.storage
    if st.backend == "local":
        if st.signing_key is not None:
            key = st.signing_key.get_secret_value().encode()
        else:
            key = _load_or_create_signing_key(settings.paths.storage_dir)
        return LocalStorage(settings.paths.storage_dir, key)

    problems = []
    if not st.r2.endpoint:
        problems.append("storage.r2.endpoint 未設定")
    if not st.r2.bucket:
        problems.append("storage.r2.bucket 未設定")
    elif not _BUCKET_NAME.fullmatch(st.r2.bucket) or ".." in st.r2.bucket:
        # bucket 名稱也是本機快取的資料夾名稱（<cache_dir>/<bucket>），只能是一段安全的路徑
        problems.append(f"storage.r2.bucket 名稱不合法：{st.r2.bucket!r}（3–63 個小寫英數字、. 或 -，頭尾是英數字）")
    if st.r2.access_key is None or not st.r2.access_key.get_secret_value():
        problems.append("R2 access key 未設定（環境變數 APP_STORAGE__R2__ACCESS_KEY）")
    if st.r2.secret_key is None or not st.r2.secret_key.get_secret_value():
        problems.append("R2 secret key 未設定（環境變數 APP_STORAGE__R2__SECRET_KEY）")
    if problems:
        raise SettingsError(problems)
    r2 = R2Storage(
        st.r2.endpoint,
        st.r2.bucket,
        st.r2.access_key.get_secret_value(),
        st.r2.secret_key.get_secret_value(),
    )
    # 快取依 bucket 分資料夾：換 bucket 時不會讀到（或驗證到）另一個 bucket 的快取內容
    cache_dir = (st.cache_dir or (settings.paths.storage_dir / "r2_cache")) / st.r2.bucket
    return CachedStorage(r2, cache_dir, st.cache_max_bytes)


_EXIF_ORIENTATION = 0x0112


def _high_depth_to_l(im: Image.Image) -> Image.Image:
    """I;16／I／F 灰階縮成 8-bit L。Pillow 直接 convert 會把超過 255 的值截掉，整張變白。"""
    if im.mode.startswith("I;16"):
        arr = np.asarray(im).astype(np.uint16) >> 8
    elif im.mode == "I":
        arr = np.clip(np.asarray(im).astype(np.int64), 0, 65535)
        if arr.max(initial=0) > 255:
            arr = arr >> 8
    else:  # F
        arr = np.nan_to_num(np.asarray(im).astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        arr = np.clip(arr, 0.0, None)
        peak = arr.max(initial=0.0)
        arr = arr * 255.0 if peak <= 1.0 else arr / peak * 255.0
        arr = np.round(arr)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))  # 2-D uint8 → L


def _flatten_on_white(im: Image.Image) -> Image.Image:
    """轉成 RGB；有透明度的圖先合成到白底；16-bit／32-bit 灰階（I;16、I、F）先縮成 8-bit。"""
    if im.mode.startswith("I") or im.mode == "F":
        return _high_depth_to_l(im).convert("RGB")
    return flatten_on_white(im)


def load_rgb(
    data: bytes,
    *,
    max_bytes: int = 10_000_000,
    max_side: int = 8192,
    max_pixels: int | None = None,
    draft_size: tuple[int, int] | None = None,
) -> tuple[Image.Image, tuple[int, int]]:
    """驗證上傳圖片（PNG/JPEG/WebP）並解碼成 RGB Image。回傳 (圖, 轉正後的原尺寸 (寬, 高))。
    max_pixels：像素數上限，只看檔頭宣告的尺寸、不解碼；超過丟 TooManyPixels。
    draft_size：（轉正後的）需要尺寸；JPEG 會以 1/2、1/4、1/8 縮小解碼，每邊仍不小於它，省時省記憶體。"""
    if len(data) > max_bytes:
        raise ImageRejected(f"檔案過大：{len(data)} bytes，上限 {max_bytes}")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise ImageRejected(f"不支援的格式：{im.format}（僅接受 PNG/JPEG/WebP）")
            w, h = im.size
            if max_pixels is not None and w * h > max_pixels:
                raise TooManyPixels(UPLOAD_TOO_MANY_PIXELS)
            if min(w, h) < MIN_SIDE:
                raise ImageRejected(f"圖片太小：{w}x{h}，最短邊至少 {MIN_SIDE}px")
            if max(w, h) > max_side:
                raise ImageRejected(f"圖片太大：{w}x{h}，最長邊上限 {max_side}px")
            rotated = im.getexif().get(_EXIF_ORIENTATION) in (5, 6, 7, 8)
            if rotated:
                w, h = h, w
            if draft_size is not None and im.format == "JPEG":
                dw, dh = draft_size
                im.draft("RGB", (dh, dw) if rotated else (dw, dh))  # draft 用檔案內的方向
            im = ImageOps.exif_transpose(im)
            rgb = _flatten_on_white(im)
    except Image.DecompressionBombError as e:
        # Pillow 在開檔時就擋下宣告尺寸超過其上限（約 1.8 億像素）的圖
        if max_pixels is not None:
            raise TooManyPixels(UPLOAD_TOO_MANY_PIXELS) from e
        raise ImageRejected(f"無法解析為圖片：{e}") from e
    except (UnidentifiedImageError, OSError) as e:
        raise ImageRejected(f"無法解析為圖片：{e}") from e
    return rgb, (w, h)
