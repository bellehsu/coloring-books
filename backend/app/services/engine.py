"""生成引擎介面：背景 worker 只依賴這裡的型別，不接觸任何 ComfyUI 節點。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.models import GenKind


@dataclass
class GenerationRequest:
    kind: GenKind
    prompt: str
    negative_prompt: str
    seed: int
    width: int
    height: int
    character_image: Path | None = None
    structure_image: Path | None = None
    structure_strength: float = 0.0
    # 只用於 POSE："precise"（qwen_edit_pose_cutout）或 "fast"（sdxl_pose_cutout）。
    # 預設 "fast"：Phase 5 的舊 request_json 沒有這個欄位，當時都是 SDXL。（API 的預設是 "precise"）
    pose_mode: str = "fast"
    variation: str | None = None  # 精準模式的動作變化語（已併入 prompt；另記錄在 params）
    # 只用於 BACKGROUND："simple"（qwen_background）或 "detailed"（sdxl_background）。
    # 預設 "detailed"：Phase 7 以前的舊 request_json 沒有這個欄位，當時都是 SDXL。風格在排入佇列時決定，引擎不再判斷。
    background_style: str = "detailed"


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
