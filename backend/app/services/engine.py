"""生成引擎介面：背景 worker 只依賴這裡的型別，不接觸任何 ComfyUI 節點。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.models import GenKind


# 已停用的生成（SDXL 路線、舊整頁流程）：排隊中的這類任務與引擎都用這句話拒絕，絕不偷換模型。
RETIRED = "這種生成已停用"


@dataclass
class GenerationRequest:
    kind: GenKind
    prompt: str
    negative_prompt: str  # Qwen 的 cfg 為 1 → 不使用；新任務一律存空字串
    seed: int
    width: int
    height: int
    character_image: Path | None = None  # 只用於 POSE：角色參考圖
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
