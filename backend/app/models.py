"""資料表：Book / Page / Generation。

頁面與生成紀錄分離：Page 只存 selected_generation_id，重新生成只新增 Generation。
時間一律為 UTC。注意：此檔不可使用 `from __future__ import annotations`（SQLModel 需要實際型別）。
"""

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel

from app.services.layout import PaperSize, PrintMode


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GenKind(str, Enum):
    CHARACTER = "character"
    PAGE = "page"


class GenStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PageStatus(str, Enum):
    DRAFT = "draft"
    PASSED = "passed"  # Phase 3 使用


class Book(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    paper_size: PaperSize
    print_mode: PrintMode
    character_description: str | None = None
    character_generation_id: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Page(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    position: int
    scene_prompt: str = ""
    controlnet_strength: float = 0.4
    sketch_path: str | None = None
    selected_generation_id: int | None = None
    status: PageStatus = PageStatus.DRAFT
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Generation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    page_id: int | None = Field(default=None, foreign_key="page.id", index=True)
    kind: GenKind
    status: GenStatus = Field(default=GenStatus.QUEUED, index=True)
    discarded: bool = False
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
    elapsed_s: float | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
