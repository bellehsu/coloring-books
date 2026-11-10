"""角色動作的精準／快速模式能不能用：只看快取的 preflight 報告，絕不即時連 ComfyUI。

規則（spec §10.3）：
- 還沒有報告，或 ComfyUI 連不上（狀態未知）→ 兩種模式都算可用；真的缺東西時由生成失敗流程回報。
- 否則該模式的 workflow 報告 ok=false → 不可用，missing 列出缺的模型檔名、節點類別與載入錯誤。
"""

from __future__ import annotations

from typing import TypedDict

from .comfy_engine import POSE_WORKFLOWS
from .preflight import PreflightReport, WorkflowReport


class ModeStatus(TypedDict):
    available: bool
    missing: list[str]


def _missing(rep: WorkflowReport) -> list[str]:
    items = [m.filename for m in rep.missing_models]
    items += [n.class_type for n in rep.missing_nodes]
    items += list(rep.load_errors)
    if not items:  # 只有輸入錯誤時也要說得出原因，不能是空清單
        items = list(rep.input_errors)
    return list(dict.fromkeys(items))  # 去重、保留順序


def availability(report: PreflightReport | None) -> dict[str, ModeStatus]:
    if report is None or not report.comfy_reachable:
        return {mode: {"available": True, "missing": []} for mode in POSE_WORKFLOWS}
    by_name = {w.name: w for w in report.workflows}
    out: dict[str, ModeStatus] = {}
    for mode, workflow in POSE_WORKFLOWS.items():
        rep = by_name.get(workflow)
        if rep is None:
            out[mode] = {"available": False, "missing": [f"workflow {workflow}"]}
        elif rep.ok:
            out[mode] = {"available": True, "missing": []}
        else:
            out[mode] = {"available": False, "missing": _missing(rep)}
    return out


def precise_unavailable_detail(report: PreflightReport | None) -> str | None:
    """精準模式不可用時的 503 訊息（「精準模式缺少：a、b」）；可用時回傳 None。"""
    status = availability(report)["precise"]
    if status["available"]:
        return None
    return "精準模式缺少：" + "、".join(status["missing"])
