"""佇列排程：依模型家族（SDXL / Qwen）分組，減少 ComfyUI 換模型。

純函式，不碰資料庫；worker 取任務與計算 queue_position 都用 plan_order，兩者因此一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models import GenKind

Family = Literal["qwen", "sdxl"]


def family_of(kind: GenKind, request_json: str) -> Family:
    """POSE 且 pose_mode == "precise" → qwen；其他（含無法解析的 request_json）→ sdxl。"""
    if GenKind(kind) != GenKind.POSE:
        return "sdxl"
    try:
        d = json.loads(request_json)
    except (TypeError, ValueError):
        return "sdxl"
    if isinstance(d, dict) and d.get("pose_mode") == "precise":
        return "qwen"
    return "sdxl"


@dataclass(frozen=True)
class QueuedJob:
    id: int
    family: str
    created_at: datetime


def plan_order(
    queued: list[QueuedJob], last_family: str | None, now: datetime, max_wait_s: float
) -> list[int]:
    """回傳執行順序（id 清單）。

    1. 等待超過 max_wait_s 的任務最先，最舊優先（防飢餓）。
    2. 再來是與 last_family 同家族的任務，最舊優先。
    3. 其餘最舊優先。last_family 為 None（例如重啟後）時即為最舊優先。
    「最舊」以 created_at 排序，相同時以 id 決定。
    """
    fifo = sorted(queued, key=lambda j: (j.created_at, j.id))
    overdue = [j for j in fifo if (now - j.created_at).total_seconds() > max_wait_s]
    rest = [j for j in fifo if (now - j.created_at).total_seconds() <= max_wait_s]
    same = [j for j in rest if last_family is not None and j.family == last_family]
    other = [j for j in rest if not (last_family is not None and j.family == last_family)]
    return [j.id for j in (*overdue, *same, *other)]
