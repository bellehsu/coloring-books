"""確定性合成：把去背的角色動作圖貼到背景圖的指定位置（純 Pillow／numpy，不需 GPU）。"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from app.services.imageio import flatten_on_white

# 合成演算法改變時 +1
COMPOSE_VERSION = "1"

# 遮罩外框的下限：短邊太小或長寬比太極端的遮罩不是正常的角色（BiRefNet 失敗），拒絕以免配置巨大影像
MIN_MASK_SIDE_PX = 16
MAX_MASK_ASPECT = 8
MAX_WIDTH_FACTOR = 3  # 合成後角色寬度上限：背景寬的幾倍
DEGENERATE_MASK = "角色動作的去背結果太細長，請重新產生角色動作"


def _check_mask_box(w: int, h: int) -> None:
    if min(w, h) < MIN_MASK_SIDE_PX or max(w, h) > MAX_MASK_ASPECT * min(w, h):
        raise ValueError(DEGENERATE_MASK)


@dataclass(frozen=True)
class Placement:
    """角色相對背景的位置：cx、cy 為角色中心（0–1），height 為角色高度／背景高度。"""

    cx: float
    cy: float
    height: float
    flip: bool

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Placement":
        """cx、cy、height 必填（int 或 float，不接受 bool、字串、NaN／inf）；
        flip 選填，缺少時為 False，有的話必須是真正的 bool。最後會呼叫 validate()。
        任何不合法的輸入都丟 ValueError（中文訊息）。"""
        if not isinstance(d, dict):
            raise ValueError("位置必須是物件")
        vals: dict[str, float] = {}
        for k in ("cx", "cy", "height"):
            if k not in d:
                raise ValueError(f"缺少欄位 {k}")
            v = d[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{k} 必須是數字")
            if not math.isfinite(v):
                raise ValueError(f"{k} 必須是有限的數字")
            vals[k] = float(v)
        flip = d.get("flip", False)
        if not isinstance(flip, bool):
            raise ValueError("flip 必須是 true 或 false")
        p = cls(cx=vals["cx"], cy=vals["cy"], height=vals["height"], flip=flip)
        p.validate()
        return p

    def to_dict(self) -> dict[str, Any]:
        return {"cx": self.cx, "cy": self.cy, "height": self.height, "flip": self.flip}

    def validate(self) -> None:
        if not 0.0 <= self.cx <= 1.0:
            raise ValueError("cx 必須介於 0 到 1")
        if not 0.0 <= self.cy <= 1.0:
            raise ValueError("cy 必須介於 0 到 1")
        if not 0.1 <= self.height <= 1.0:
            raise ValueError("height 必須介於 0.1 到 1.0")

    def key(self) -> tuple[float, float, float, bool]:
        """比對用：四捨五入到小數 4 位。"""
        return (round(self.cx, 4), round(self.cy, 4), round(self.height, 4), self.flip)


DEFAULT_PLACEMENT = Placement(0.5, 0.66, 0.6, False)


def page_placement(placement_json: str | None) -> Placement:
    """頁面的位置草稿；還沒存過時用預設位置（與確認合成時相同）。"""
    return Placement.from_dict(json.loads(placement_json)) if placement_json else DEFAULT_PLACEMENT


def mask_bbox(mask: Image.Image) -> tuple[int, int, int, int] | None:
    """遮罩 > 128 的外接框；全空時 None。"""
    return mask.convert("L").point(lambda v: 255 if v > 128 else 0).getbbox()


def cutout(pose: Image.Image, mask: Image.Image) -> Image.Image:
    """角色圖裁到遮罩外接框的 RGBA，alpha＝遮罩。尺寸不同或遮罩全空時丟 ValueError。"""
    rgb = pose.convert("RGB")
    m = mask.convert("L")
    if m.size != rgb.size:
        raise ValueError("遮罩與角色圖尺寸不同")
    bbox = mask_bbox(m)
    if bbox is None:
        raise ValueError("遮罩是空的")
    out = rgb.crop(bbox).convert("RGBA")
    out.putalpha(m.crop(bbox))
    return out


def compose(
    background: Image.Image, pose: Image.Image, mask: Image.Image, placement: Placement
) -> Image.Image:
    """回傳與背景同尺寸的 RGB 合成圖。超出背景的部分會被裁掉。

    只使用 mask 決定角色輪廓；pose 本身的 alpha 會被忽略（直接丟掉 alpha 通道）。
    RGBA 背景會先壓平鋪在白底上。mask 與 pose 尺寸必須相同。
    """
    bg = flatten_on_white(background)
    cut = cutout(pose, mask)  # 尺寸不同或遮罩全空時丟 ValueError
    _check_mask_box(*cut.size)
    pose_rgb = cut.convert("RGB")  # 只留 RGB；輪廓用 alpha（＝裁好的遮罩）
    m = cut.getchannel("A")

    target_h = max(1, round(placement.height * bg.height))
    target_w = min(max(1, round(pose_rgb.width * target_h / pose_rgb.height)), MAX_WIDTH_FACTOR * bg.width)
    pose_rgb = pose_rgb.resize((target_w, target_h), Image.LANCZOS)
    m = m.resize((target_w, target_h), Image.BILINEAR)
    if placement.flip:
        pose_rgb = ImageOps.mirror(pose_rgb)
        m = ImageOps.mirror(m)
    m = m.filter(ImageFilter.GaussianBlur(1))

    x = round(placement.cx * bg.width - target_w / 2)
    y = round(placement.cy * bg.height - target_h / 2)
    out = bg.copy()
    out.paste(pose_rgb, (x, y), m)  # paste 會自動裁掉超出邊界的部分
    return out


def _fill_holes(binary: np.ndarray) -> np.ndarray:
    """補滿前景（8 連通）內部被圍住的洞：從圖外一圈以 4 連通淹沒背景，淹不到的背景就是洞。
    圖邊也算「外面」，所以被圖邊切到的角色和圖邊圍出的區域不會被補。"""
    h, w = binary.shape
    pad = np.zeros((h + 2, w + 2), np.uint8)
    pad[1:-1, 1:-1] = binary
    flood_mask = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(pad, flood_mask, (0, 0), 1, flags=4)  # 外面的背景塗成 1
    return binary | (pad[1:-1, 1:-1] == 0).astype(np.uint8)


def largest_component(mask_png: bytes) -> bytes:
    """只保留遮罩中最大的 8 連通區塊（>128 視為前景），並補滿它內部的洞，輸出同尺寸 L 模式 PNG。
    該區塊（補洞後）向外膨脹 2 px 的範圍內保留原本的灰階值（BiRefNet 的柔邊），其餘一律 0；
    用來丟掉與角色分離的陰影等碎塊。洞裡的像素（含洞內緣的柔邊、洞中的碎塊）一律 255：
    Qwen 平塗風格的白色臉、頭盔內部常被 BiRefNet 當成背景挖掉（phase9_qwen_character_zh_20261110_1502）。
    全黑遮罩原樣回傳全黑。"""
    with Image.open(io.BytesIO(mask_png)) as im:
        arr = np.asarray(im.convert("L"))
    binary = (arr > 128).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out = np.zeros_like(arr)
    if n > 1:
        best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        _check_mask_box(int(stats[best, cv2.CC_STAT_WIDTH]), int(stats[best, cv2.CC_STAT_HEIGHT]))
        main = (labels == best).astype(np.uint8)
        filled = _fill_holes(main)
        keep = cv2.dilate(filled, np.ones((5, 5), np.uint8))
        out[keep > 0] = arr[keep > 0]
        out[(filled > 0) & (main == 0)] = 255
    buf = io.BytesIO()
    Image.fromarray(out, "L").save(buf, "PNG")
    return buf.getvalue()
