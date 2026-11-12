"""生成引擎介面：背景 worker 只依賴這裡的型別，不接觸任何 ComfyUI 節點。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.models import GenKind


# 已停用的生成（SDXL 路線、舊整頁流程）：排隊中的這類任務與引擎都用這句話拒絕，絕不偷換模型。
RETIRED = "這種生成已停用"


@dataclass
class GenerationRequest:
    kind: GenKind
    prompt: str
    seed: int
    width: int
    height: int
    character_image: bytes | None = None  # 只用於 POSE：角色參考圖的 PNG bytes（worker 從 Storage 讀出）
    variation: str | None = None  # 變化語（已併入 prompt；另記錄在 params）


@dataclass
class GenerationResult:
    image: bytes
    elapsed_s: float
    engine: str
    workflow_name: str
    workflow_version: str
    workflow_hash: str
    models: list[str]
    params: dict
    extra_images: dict[str, bytes] = field(default_factory=dict)  # 例如 pose 的 "mask"


class EngineError(Exception):
    """生成失敗（不可重試或原因在請求本身）。"""


class EngineUnavailable(EngineError):
    """引擎連不上，可稍後重試。"""


ProgressFn = Callable[[int, int], None]


class GenerationEngine(Protocol):
    async def generate(self, req: GenerationRequest, on_progress: ProgressFn | None = None) -> GenerationResult: ...

    async def interrupt(self) -> None:
        """盡力中斷執行中的生成（後端停止時呼叫）；沒有東西可中斷或失敗都不丟例外。"""
        ...


ENGINE_UNREACHABLE_PUBLIC = "繪圖服務暫時無法連線，稍後會自動重試"
ENGINE_FAILED_PUBLIC = "繪圖時發生錯誤，請再試一次"
_INTERNAL_MARKERS = ("127.0.0.1", "localhost", "http://", "https://")
_PATH_MARKERS = ("/ai-data/", "/home/", "/tmp/", "/opt/")
_DRIVE_PATH = re.compile(r"[A-Za-z]:\\")


def public_engine_error(text: str | None, is_admin: bool) -> str | None:
    """給 API 回應用：非管理者看不到引擎的內部位址、連線細節與檔案路徑，
    含位址的錯誤原文換成連不上的固定訊息，含路徑的換成繪圖失敗的固定訊息。"""
    if text is None or is_admin:
        return text
    if any(m in text for m in _INTERNAL_MARKERS):
        return ENGINE_UNREACHABLE_PUBLIC
    if any(m in text for m in _PATH_MARKERS) or _DRIVE_PATH.search(text):
        return ENGINE_FAILED_PUBLIC
    return text
