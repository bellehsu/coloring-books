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
