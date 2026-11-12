"""自動補線（舊編輯器的「自動補線」與頁面流程共用）：對 QA 的缺口補 draw 筆畫，寫在 auto 修線圖層。

渲染順序是 底稿 → auto 層 → user 層，所以試補線也照這個順序：在「底稿＋auto 層」上加補線，最後才套
user 層；會被使用者橡皮擦擦到的補線不補（補了也接不起來，只會一直累積無用的 auto 筆畫）。
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from app.services.lineart_render import Stroke, apply_strokes, stroke_patch
from app.services.qa import evaluate

AUTOFIX_MAX_PASSES = 3  # ruling 2026-11-06：補線會讓別的近缺口變成缺口，所以重複幾輪

# 每一層修線各自的上限（PUT 修線的驗證也用這兩個數字）
MAX_STROKES = 500
MAX_TOTAL_POINTS = 20000


def read_ink(data: bytes) -> np.ndarray:
    """線稿 PNG → 墨水陣列（True＝黑）。"""
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("L")) < 128


def bridge_stroke(bridge, w: int, h: int, width_px: int) -> Stroke:
    """QA 的相對座標是 x/w；apply_strokes 用 x*(w-1)，換算成同一個像素。"""
    def conv(x: float, y: float) -> tuple[float, float]:
        return (min(1.0, max(0.0, round(x * w) / (w - 1))), min(1.0, max(0.0, round(y * h) / (h - 1))))

    (ex, ey), (qx, qy) = bridge
    return Stroke("draw", float(width_px), (conv(ex, ey), conv(qx, qy)))


def survives(erased: np.ndarray | None, stroke: Stroke) -> bool:
    """補線 stroke 不會被 user 層擦掉（erased＝user 層最後擦成白的像素；None＝沒有 user 層）。
    只比對筆畫外框內的子陣列。"""
    if erased is None:
        return True
    patch = stroke_patch(stroke, erased.shape[1], erased.shape[0])
    if patch is None:
        return True
    x0, y0, m = patch
    return not (m & erased[y0:y0 + m.shape[0], x0:x0 + m.shape[1]]).any()


def plan_autofix(
    pre: np.ndarray,
    user: list[Stroke],
    gap_count: int,
    bridges: list,
    width_px: int,
    *,
    room_strokes: int,
    room_points: int,
) -> list[Stroke]:
    """回傳要加在 auto 層最後面的補線（可能是空的）。

    pre：底稿＋目前 auto 層的墨水；user：user 層筆畫；gap_count／bridges：pre 套上 user 層後的 QA。
    每輪對每個缺口補一筆，重算 QA，最多 AUTOFIX_MAX_PASSES 輪；只保留讓缺口數減少的那幾輪，
    某輪沒減少（或沒有缺口了）就停。room_strokes／room_points 是這一層還剩的筆數與點數。"""
    erased = ~apply_strokes(np.ones_like(pre), user) if user else None  # user 層最後會擦成白的像素
    h, w = pre.shape

    added: list[Stroke] = []
    for _ in range(AUTOFIX_MAX_PASSES):
        room = min(room_strokes - len(added), (room_points - 2 * len(added)) // 2)
        batch = [s for s in (bridge_stroke(b, w, h, width_px) for b in bridges[:max(0, room)]) if survives(erased, s)]
        if not batch:
            break
        trial_pre = apply_strokes(pre, batch)
        report = evaluate(apply_strokes(trial_pre, user) if user else trial_pre, width_px)
        if report.gap_count >= gap_count:
            break  # 這一輪沒有進展：不留它的筆畫
        pre, added = trial_pre, added + batch
        gap_count, bridges = report.gap_count, report.bridges
        if gap_count == 0:
            break
    return added
