"""背景風格：簡潔（qwen_background）／細緻（sdxl_background）。

排隊當下決定實際風格（spec 2026-11-10 §3.1），引擎只照 request_json 的 background_style 走、不再判斷：
1. 頁面有可用的構圖草圖（有草圖且 controlnet_strength > 0）→ 細緻，不管要求什麼（只有 SDXL 會照草圖構圖）；
2. 要求簡潔但快取的 preflight 說 qwen_background 不可用 → 細緻；
3. 其他 → 要求的風格。
可用性與角色動作模式同一套規則（只看快取的 preflight，不連 ComfyUI）。
"""

from __future__ import annotations

from typing import Literal

from .comfy_engine import BACKGROUND_WORKFLOWS
from .pose_modes import ModeStatus, availability
from .preflight import PreflightReport

BackgroundStyle = Literal["simple", "detailed"]


def decide_style(
    requested: BackgroundStyle, *, has_usable_sketch: bool, simple_available: bool
) -> BackgroundStyle:
    if has_usable_sketch:
        return "detailed"
    if requested == "simple" and not simple_available:
        return "detailed"
    return requested


def style_availability(report: PreflightReport | None) -> dict[str, ModeStatus]:
    return availability(report, BACKGROUND_WORKFLOWS)
