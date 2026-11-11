"""資料表：Owner / OwnerSession / Usage / Book / Page / Generation。

頁面與生成紀錄分離：Page 只存 selected_generation_id，重新生成只新增 Generation。
時間一律為 UTC。注意：此檔不可使用 `from __future__ import annotations`（SQLModel 需要實際型別）。
"""

from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Column, DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from app.services.layout import PaperSize, PrintMode
from app.services.lineart_render import LineDetail


# SQLite INTEGER（64 位元有號）的範圍：超出的 id 傳進查詢會丟 OverflowError（API 要先擋成 404／422）
SQLITE_INT_MIN = -(2**63)
SQLITE_INT_MAX = 2**63 - 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite 不保存時區；存入時轉 UTC，讀出時補回 tzinfo=UTC。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime 必須帶時區（UTC）")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


def _utc_col() -> Column:
    return Column(UTCDateTime(), nullable=False)


class GenKind(str, Enum):
    CHARACTER = "character"
    PAGE = "page"
    BACKGROUND = "background"
    POSE = "pose"
    COMPOSITE = "composite"


class GenStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PageStatus(str, Enum):
    DRAFT = "draft"
    PASSED = "passed"  # Phase 3 使用


class CharacterCheck(str, Enum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"


class Owner(SQLModel, table=True):
    """一個邀請連結＝一個 owner。DB 只存 token 的 sha256。"""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    token_hash: str = Field(unique=True)
    is_admin: bool = False
    daily_generation_cap: int = 60
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())
    revoked_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime(), nullable=True))


class OwnerSession(SQLModel, table=True):
    """cookie 裡是隨機 session id；DB 只存它的 sha256。"""

    __tablename__ = "owner_session"

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="owner.id", index=True)
    session_hash: str = Field(unique=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())
    last_seen: datetime = Field(default_factory=utcnow, sa_column=_utc_col())


class Usage(SQLModel, table=True):
    """每個 owner 每天（Asia/Taipei）已排入的生成數。"""

    owner_id: int = Field(foreign_key="owner.id", primary_key=True)
    day: date = Field(primary_key=True)
    generations: int = 0


class Book(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="owner.id", index=True)  # 只有書有 owner；頁面／生成紀錄經 book_id 繼承
    title: str
    paper_size: PaperSize
    print_mode: PrintMode
    character_description: str | None = None
    character_generation_id: int | None = None
    shared_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime(), nullable=True))  # null＝不分享
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())


class Page(SQLModel, table=True):
    # 刪除頁面會連同生成紀錄硬刪除；AUTOINCREMENT 保證 id 永不沿用，前端以 id 為 key 的狀態才不會串到新頁面
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    position: int
    scene_prompt: str = ""
    action_prompt: str = Field(default="", sa_column_kwargs={"server_default": ""})
    selected_generation_id: int | None = None
    background_generation_id: int | None = None
    pose_generation_id: int | None = None
    placement_json: str | None = None  # 角色在背景上的位置（JSON：cx, cy, height, flip）
    status: PageStatus = PageStatus.DRAFT
    line_detail: LineDetail = LineDetail.STANDARD
    line_width_px: int = 11
    character_check: CharacterCheck | None = None
    qa_note: str = ""
    forced_pass_reason: str | None = None
    passed_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime(), nullable=True))
    lineart_key: str | None = None
    lineart_path: str | None = None
    qa_json: str | None = None
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())


class Generation(SQLModel, table=True):
    __table_args__ = {"sqlite_autoincrement": True}  # 同 Page：id 永不沿用

    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    page_id: int | None = Field(default=None, foreign_key="page.id", index=True)
    kind: GenKind
    status: GenStatus = Field(default=GenStatus.QUEUED, index=True)
    discarded: bool = False
    discarded_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime(), nullable=True))  # 保留期限從這裡起算
    user_text: str  # 使用者輸入的原文（角色描述或場景描述）
    prompt: str
    negative_prompt: str
    seed: int
    width: int
    height: int
    request_json: str  # 引擎請求的邏輯參數（JSON）
    engine: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    workflow_hash: str | None = None
    models_json: str | None = None
    params_json: str | None = None  # 實際送出的全部 manifest 參數
    image_path: str | None = None
    mask_path: str | None = None  # pose：去背遮罩
    elapsed_s: float | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())
    started_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime(), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime(), nullable=True))


class LineartEdit(SQLModel, table=True):
    """修線圖層跟著生成圖走：換回舊候選圖時，它的修線也回來。"""

    generation_id: int = Field(primary_key=True, foreign_key="generation.id")
    page_id: int = Field(foreign_key="page.id", index=True)
    strokes_json: str = "[]"
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_utc_col())


class AppMeta(SQLModel, table=True):
    """小型 key-value 設定（例如 storage_instance_id：DB 與 storage 的身分標記）。"""

    __tablename__ = "app_meta"

    key: str = Field(primary_key=True)
    value: str
