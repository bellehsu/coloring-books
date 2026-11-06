#!/usr/bin/env python3
"""產生 Route C 第二輪用的「姿勢／構圖草圖」（白底黑線，給 ControlNet Union scribble 模式）。

Route A workflow 會把結構圖反相成黑底白線，所以這裡一律畫白底黑線。
比例以角色參考圖為準（大頭身：頭＋耳約佔全身 45%），讓 ControlNet 只提供姿勢與道具位置，
細節交給 IP-Adapter 與提示詞。
每個場景輸出兩張：<場景>.png（含火柴人角色）與 <場景>_props.png（只有道具與背景，角色區域留白；
C2 發現火柴人會把角色比例拉壞，C3 改用 _props 只控制場景）。

用法：python experiments/pose_sketches.py [輸出資料夾]（預設 experiments/samples/pose）
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

W, H = 832, 1216
LINE = 6
SCENES = ("park_swing", "kitchen_baking", "bedroom_reading")


def _canvas() -> np.ndarray:
    return np.full((H, W), 255, np.uint8)


def _l(img, p1, p2, t=LINE):
    cv2.line(img, tuple(map(int, p1)), tuple(map(int, p2)), 0, t, cv2.LINE_AA)


def _head(img, cx, cy, r):
    """頭＋兩隻直立兔耳。"""
    cv2.circle(img, (int(cx), int(cy)), int(r), 0, LINE, cv2.LINE_AA)
    for dx in (-0.45, 0.45):
        ex = cx + dx * r
        cv2.ellipse(img, (int(ex), int(cy - 1.55 * r)), (int(0.28 * r), int(0.75 * r)), 0, 0, 360, 0, LINE,
                    cv2.LINE_AA)


def park_swing(character: bool = True) -> np.ndarray:
    img = _canvas()
    # 鞦韆架（左右立柱在角色兩側，避免壓到臉）
    _l(img, (60, 60), (W - 60, 60), 10)
    _l(img, (90, 60), (60, H - 140), 10)
    _l(img, (W - 90, 60), (W - 60, H - 140), 10)
    _l(img, (0, H - 140), (W, H - 140), 4)  # 地面
    # 繩子與座板
    seat_y = 800
    _l(img, (W / 2 - 170, 60), (W / 2 - 170, seat_y))
    _l(img, (W / 2 + 170, 60), (W / 2 + 170, seat_y))
    cv2.rectangle(img, (int(W / 2 - 200), seat_y), (int(W / 2 + 200), seat_y + 30), 0, LINE)
    if character:  # 角色坐在座板上：頭、身體、雙手抓繩、腿往前垂
        cx, r = W / 2, 125
        _head(img, cx, 450, r)
        cv2.ellipse(img, (int(cx), 700), (115, 110), 0, 0, 360, 0, LINE, cv2.LINE_AA)  # 身體
        _l(img, (cx - 100, 640), (cx - 170, 600))  # 手臂抓繩
        _l(img, (cx + 100, 640), (cx + 170, 600))
        for dx in (-60, 60):
            _l(img, (cx + dx, seat_y + 30), (cx + dx * 1.3, 960))  # 小腿
            cv2.ellipse(img, (int(cx + dx * 1.3), 975), (40, 22), 0, 0, 360, 0, LINE)  # 鞋
    return img


def kitchen_baking(character: bool = True) -> np.ndarray:
    img = _canvas()
    # 吊櫃與檯面（左側），烤箱
    cv2.rectangle(img, (0, 80), (420, 260), 0, LINE)
    cv2.rectangle(img, (0, 720), (440, H - 120), 0, LINE)
    _l(img, (0, 720), (460, 720), 10)
    cv2.rectangle(img, (60, 820), (380, 1040), 0, LINE)  # 烤箱門
    cv2.circle(img, (200, 650), 70, 0, LINE)  # 攪拌碗（檯面上）
    _l(img, (130, 650), (270, 650))
    _l(img, (0, H - 120), (W, H - 120), 4)  # 地板
    if character:  # 角色站在右側、面向左，雙手捧烤盤（烤盤由角色拿著，算角色的一部分）
        cx, r = 600, 125
        _head(img, cx, 420, r)
        cv2.ellipse(img, (cx, 690), (110, 140), 0, 0, 360, 0, LINE, cv2.LINE_AA)
        _l(img, (cx - 90, 640), (cx - 190, 700))
        _l(img, (cx + 60, 650), (cx - 120, 715))
        cv2.rectangle(img, (cx - 290, 700), (cx - 90, 730), 0, LINE)  # 烤盤
        for x in (cx - 255, cx - 195, cx - 135):
            cv2.circle(img, (x, 688), 18, 0, 4)  # 餅乾
        for dx in (-45, 45):
            _l(img, (cx + dx, 820), (cx + dx, 1010))
            cv2.ellipse(img, (cx + dx - 10, 1025), (45, 22), 0, 0, 360, 0, LINE)
    return img


def bedroom_reading(character: bool = True) -> np.ndarray:
    img = _canvas()
    # 窗戶（右上）與月亮、星星
    cv2.rectangle(img, (500, 60), (790, 400), 0, LINE)
    _l(img, (645, 60), (645, 400))
    _l(img, (500, 230), (790, 230))
    cv2.circle(img, (580, 140), 40, 0, LINE)
    cv2.circle(img, (600, 125), 36, 255, -1)  # 新月
    for x, y in ((720, 120), (700, 300), (560, 330)):
        _l(img, (x - 12, y), (x + 12, y), 4)
        _l(img, (x, y - 12), (x, y + 12), 4)
    # 床：床頭板（左）、床墊、被子
    cv2.rectangle(img, (30, 520), (110, 1060), 0, LINE)
    cv2.rectangle(img, (110, 850), (800, 960), 0, LINE)
    _l(img, (110, 960), (110, 1080))
    _l(img, (800, 960), (800, 1080))
    # 床頭櫃與檯燈（右下）
    # 角色坐在床上靠著枕頭，被子蓋到腰，雙手拿打開的書
    cv2.ellipse(img, (200, 760), (90, 60), 0, 0, 360, 0, LINE)  # 枕頭
    if character:  # 頭、上半身、打開的書
        cx, r = 330, 120
        _head(img, cx, 520, r)
        cv2.ellipse(img, (cx, 760), (100, 110), 0, 180, 360, 0, LINE, cv2.LINE_AA)
        pts = np.array([[cx + 60, 760], [cx + 170, 700], [cx + 280, 760], [cx + 170, 790]], np.int32)
        cv2.polylines(img, [pts], True, 0, LINE, cv2.LINE_AA)  # 打開的書
        _l(img, (cx + 170, 700), (cx + 170, 790))
    cv2.ellipse(img, (450, 850), (350, 60), 0, 180, 360, 0, LINE)  # 被子隆起
    cv2.rectangle(img, (690, 640), (780, 760), 0, LINE)  # 檯燈燈罩
    _l(img, (735, 760), (735, 850))
    return img


def make_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in SCENES:
        for suffix, character in (("", True), ("_props", False)):  # _props：只有道具與背景，角色區域留白
            path = out_dir / f"{name}{suffix}.png"
            cv2.imwrite(str(path), globals()[name](character=character))
            out[f"{name}{suffix}"] = path
    return out


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    dst = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "samples" / "pose"
    for k, v in make_all(dst).items():
        print(k, v)
