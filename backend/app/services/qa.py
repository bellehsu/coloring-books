"""線稿品質檢查（QA）：線條閉合度、缺口偵測與 QA 報告。

著色本的核心要求是線條封閉，蠟筆或填色才不會「漏出去」。做法：

1. 骨架化（skimage skeletonize），8-連通下「恰好一個鄰居」的骨架像素就是線頭（端點）。
   - 離圖邊 border_px 以內的端點不算（線條本來就可能延伸出畫面）。
   - 孤立的單一骨架像素（0 個鄰居）不算端點：那是實心小點（例如塗黑的瞳孔）骨架化後的殘留，
     不是斷掉的線；若算進去，眼睛裡的瞳孔會被誤判成缺口。
2. 第一階段（near-miss 候選）：對每個端點 e 沿骨架做 BFS（路徑長上限 3 × max_gap_px），
   在 e 周圍半徑 max_gap_px 內找「不是沿著自己這條線走得到」的最近骨架像素 q → 候選 (e, q)。
   涵蓋「兩個線頭距離小於 N px」與「線頭靠近另一條線」。
3. 第二階段（漏色判定）：缺口必須真的會漏色，而不只是差一點接上（例如髮絲末端停在輪廓旁，
   沒有圍住任何東西）。把所有候選橋一次畫上去，白色區域 4-連通標記；橋兩側屬於不同區域、
   且兩區面積都 >= lineart_post 的 min_region_area 才算缺口。
4. 彼此距離 < max_gap_px 的標記只留一個（兩個面對面的線頭算一個缺口）。
   gap_count / gaps 只算會漏色的缺口；near_miss_count 為第一階段（合併後）的數量。

距離以骨架計算：兩條線寬 w 的圓頭線，畫面上看到的白縫寬度約為骨架距離 − w。

所有門檻集中在 QAThresholds（初始值，之後以真實圖片校正）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from skimage.morphology import skeletonize

from app.services.lineart_post import LineartParams, compute_metrics

QA_MESSAGES: dict[str, str] = {
    "gaps": "線條有缺口，塗色會漏出去，需要修線",
    "black_fill": "有大片黑色色塊",
    "tiny_regions": "太多過小的區域，小孩塗不進去",
    "fragments": "雜點或斷筆偏多",
}

# 報告中保留的 compute_metrics 指標（gray_pixel_ratio 只對原始灰階圖有意義，這裡不收）
_METRIC_KEYS = ("region_count", "closed_region_count", "tiny_region_count", "fragment_count",
                "black_fill_ratio", "black_pixel_ratio")

_NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


@dataclass(frozen=True)
class QAThresholds:
    max_gap_px: int = 40  # 骨架距離小於此值視為缺口；更大的開口視為刻意留白
    border_px: int = 12  # 離圖邊這麼近的端點不算
    gap_error_min: int = 1  # 缺口數 >= 此值 → 錯誤「需要修線」
    black_fill_warn: float = 0.005  # black_fill_ratio > 此值 → 警告
    tiny_region_warn: int = 150  # tiny_region_count > 此值 → 警告
    fragment_warn: int = 80  # fragment_count > 此值 → 警告


@dataclass
class QAReport:
    errors: list[str]
    warnings: list[str]
    gaps: list[tuple[float, float]]  # 會漏色的缺口位置，相對座標 (x/w, y/h)
    dangling_count: int
    gap_count: int
    metrics: dict[str, Any] = field(default_factory=dict)
    near_miss_count: int = 0  # 第一階段「差一點接上」的標記數（含不會漏色的），合併後

    def to_dict(self) -> dict[str, Any]:
        return {
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "gaps": [[x, y] for x, y in self.gaps],
            "dangling_count": self.dangling_count,
            "gap_count": self.gap_count,
            "metrics": dict(self.metrics),
            "near_miss_count": self.near_miss_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QAReport":
        return cls(
            errors=list(d["errors"]),
            warnings=list(d["warnings"]),
            gaps=[(float(x), float(y)) for x, y in d["gaps"]],
            dangling_count=int(d["dangling_count"]),
            gap_count=int(d["gap_count"]),
            metrics=dict(d.get("metrics", {})),
            near_miss_count=int(d.get("near_miss_count", 0)),
        )


def find_endpoints(ink: np.ndarray, border_px: int = QAThresholds.border_px
                   ) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """回傳 (骨架 bool 陣列, 端點 [(x, y), ...])。

    端點＝8-連通下恰好一個鄰居的骨架像素；孤立像素（0 鄰居）與離圖邊 border_px 內的端點不算。
    """
    ink = np.asarray(ink, dtype=bool)
    skel = skeletonize(ink) if ink.any() else np.zeros_like(ink)
    s8 = skel.astype(np.uint8)
    # 3×3 鄰域總和（含自己），邊界外視為白
    counts = cv2.filter2D(s8, cv2.CV_16S, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT)
    ys, xs = np.nonzero(skel & (counts == 2))
    h, w = skel.shape
    b = max(0, border_px)
    keep = (xs >= b) & (xs < w - b) & (ys >= b) & (ys < h - b)
    return skel, [(int(x), int(y)) for x, y in zip(xs[keep], ys[keep])]


def _reached_from(win: np.ndarray, sy: int, sx: int, limit: int) -> np.ndarray:
    """在視窗 win 內，從 (sy, sx) 沿骨架 8-連通 BFS，路徑長 <= limit 可到的像素。"""
    wh, ww = win.shape
    reached = np.zeros_like(win)
    reached[sy, sx] = True
    frontier = [(sy, sx)]
    item, seen = win.item, reached.item
    for _ in range(limit):
        nxt = []
        for y, x in frontier:
            for dy, dx in _NEIGHBOURS:
                ny, nx = y + dy, x + dx
                if 0 <= ny < wh and 0 <= nx < ww and item(ny, nx) and not seen(ny, nx):
                    reached[ny, nx] = True
                    nxt.append((ny, nx))
        if not nxt:
            break
        frontier = nxt
    return reached


Point = tuple[int, int]
Pair = tuple[Point, Point]


def _merge(marks: list[Point], min_dist: int) -> list[Point]:
    """彼此距離 < min_dist 的標記只留第一個。"""
    r2 = min_dist * min_dist
    merged: list[Point] = []
    for m in marks:
        if all((m[0] - k[0]) ** 2 + (m[1] - k[1]) ** 2 >= r2 for k in merged):
            merged.append(m)
    return merged


def _midpoint(e: Point, q: Point) -> Point:
    return (e[0] + q[0]) // 2, (e[1] + q[1]) // 2


def find_candidates(skel: np.ndarray, endpoints: list[Point], max_gap_px: int) -> list[Pair]:
    """第一階段：回傳每個「差一點接上」的端點 e 與它最近的、沿自己線條走不到的骨架像素 q，[(e, q), ...]。"""
    if max_gap_px <= 0 or not endpoints:
        return []
    skel = np.asarray(skel, dtype=bool)
    h, w = skel.shape
    limit = 3 * max_gap_px  # BFS 路徑長上限；路徑長 <= limit 的像素一定落在 ±limit 的視窗內
    r2 = max_gap_px * max_gap_px
    pairs: list[Pair] = []
    for ex, ey in endpoints:
        x0, y0 = max(0, ex - limit), max(0, ey - limit)
        win = skel[y0:min(h, ey + limit + 1), x0:min(w, ex + limit + 1)]
        cx, cy = ex - x0, ey - y0
        reached = _reached_from(win, cy, cx, limit)
        # 半徑 max_gap_px 的小視窗內找未到達的最近骨架像素
        sy0, sx0 = max(0, cy - max_gap_px), max(0, cx - max_gap_px)
        sy1, sx1 = cy + max_gap_px + 1, cx + max_gap_px + 1
        cand = win[sy0:sy1, sx0:sx1] & ~reached[sy0:sy1, sx0:sx1]
        qy, qx = np.nonzero(cand)
        if not len(qy):
            continue
        qy, qx = qy + sy0, qx + sx0
        d2 = (qx - cx) ** 2 + (qy - cy) ** 2
        i = int(np.argmin(d2))
        if d2[i] > r2:
            continue
        pairs.append(((ex, ey), (int(qx[i]) + x0, int(qy[i]) + y0)))
    return pairs


def find_gaps(skel: np.ndarray, endpoints: list[Point], max_gap_px: int) -> list[Point]:
    """第一階段標記（near-miss）：候選對的中點，已合併彼此距離 < max_gap_px 的標記。像素座標 (x, y)。"""
    return _merge([_midpoint(e, q) for e, q in find_candidates(skel, endpoints, max_gap_px)], max_gap_px)


def _side_label(labels: np.ndarray, x: float, y: float, nx: float, ny: float, start: int, span: int) -> int:
    """從 (x, y) 沿 (nx, ny) 方向、距離 start..start+span 找第一個白色像素的標籤；找不到回傳 0。"""
    h, w = labels.shape
    for t in range(start, start + span + 1):
        px, py = int(round(x + nx * t)), int(round(y + ny * t))
        if not (0 <= px < w and 0 <= py < h):
            return 0
        lab = int(labels[py, px])
        if lab:
            return lab
    return 0


def find_leaks(ink: np.ndarray, candidates: list[Pair], line_width_px: int, min_region_area: int,
               merge_px: int) -> list[Point]:
    """第二階段：只保留真的會漏色的候選，回傳合併後的缺口標記（像素座標）。

    把所有候選橋（e→q，粗細 = 線寬）一次畫上去，對白色區域做 4-連通標記；
    在每座橋中點的兩側（垂直方向、距離約一個線寬，落在線上時再往外找）取樣，
    兩側屬於不同白色區域、且兩區面積都 >= min_region_area 才算缺口
    （補上這座橋才會把兩塊可塗色區域分開 → 現在顏色會從這裡漏過去）。
    """
    if not candidates:
        return []
    lw = max(1, int(line_width_px))
    bridged = np.asarray(ink, dtype=bool).astype(np.uint8)
    for e, q in candidates:
        cv2.line(bridged, e, q, 1, lw)
    n, labels, stats, _ = cv2.connectedComponentsWithStats((bridged == 0).astype(np.uint8), connectivity=4)
    area = stats[:, cv2.CC_STAT_AREA]
    leaks: list[Point] = []
    for e, q in candidates:
        dx, dy = q[0] - e[0], q[1] - e[1]
        length = float(np.hypot(dx, dy)) or 1.0
        nx, ny = -dy / length, dx / length  # 垂直於橋的單位向量
        mx, my = (e[0] + q[0]) / 2, (e[1] + q[1]) / 2
        a = _side_label(labels, mx, my, nx, ny, lw, 2 * lw)
        b = _side_label(labels, mx, my, -nx, -ny, lw, 2 * lw)
        if a and b and a != b and area[a] >= min_region_area and area[b] >= min_region_area:
            leaks.append(_midpoint(e, q))
    return _merge(leaks, merge_px)


def evaluate(ink: np.ndarray, line_width_px: int, thresholds: QAThresholds = QAThresholds()) -> QAReport:
    ink = np.asarray(ink, dtype=bool)
    h, w = ink.shape
    params = LineartParams(line_width_px=line_width_px)
    skel, endpoints = find_endpoints(ink, thresholds.border_px)
    candidates = find_candidates(skel, endpoints, thresholds.max_gap_px)
    near_miss = _merge([_midpoint(e, q) for e, q in candidates], thresholds.max_gap_px)
    gap_px = find_leaks(ink, candidates, line_width_px, params.min_region_area, thresholds.max_gap_px)

    all_metrics = compute_metrics(ink, params)
    metrics: dict[str, Any] = {k: all_metrics[k] for k in _METRIC_KEYS}
    metrics["gap_count"] = len(gap_px)
    metrics["near_miss_count"] = len(near_miss)
    metrics["dangling_count"] = len(endpoints)

    errors = ["gaps"] if len(gap_px) >= thresholds.gap_error_min else []
    warnings = []
    if metrics["black_fill_ratio"] > thresholds.black_fill_warn:
        warnings.append("black_fill")
    if metrics["tiny_region_count"] > thresholds.tiny_region_warn:
        warnings.append("tiny_regions")
    if metrics["fragment_count"] > thresholds.fragment_warn:
        warnings.append("fragments")

    return QAReport(
        errors=errors,
        warnings=warnings,
        gaps=[(x / w, y / h) for x, y in gap_px],
        dangling_count=len(endpoints),
        gap_count=len(gap_px),
        metrics=metrics,
        near_miss_count=len(near_miss),
    )
