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

from app.models import Generation, GenKind

# 合成演算法改變時 +1
COMPOSE_VERSION = "1"


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


def composite_matches(gen: Generation, bg_id: int, pose_id: int, placement: Placement) -> bool:
    """gen（合成圖 generation）是否正是以這組背景＋角色動作＋位置（Placement.key()）
    用目前版本（COMPOSE_VERSION）的演算法合成的。只看紀錄，不檢查檔案或狀態。"""
    if gen.kind != GenKind.COMPOSITE or gen.workflow_version != COMPOSE_VERSION:
        return False
    try:
        p = json.loads(gen.params_json or "")
        return (p["background_generation_id"] == bg_id and p["pose_generation_id"] == pose_id
                and Placement.from_dict(p["placement"]).key() == placement.key())
    except (ValueError, KeyError, TypeError):
        return False


def _flatten(img: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """有透明度的圖先鋪在指定底色上再轉 RGB（避免透明處變黑）。"""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, color + (255,))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return img.convert("RGB")


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
    bg = _flatten(background, (255, 255, 255))
    pose_rgb = pose.convert("RGB")  # 直接丟掉 alpha，只留 RGB
    m = mask.convert("L")
    if m.size != pose_rgb.size:
        raise ValueError("遮罩與角色圖尺寸不同")
    bbox = mask_bbox(m)
    if bbox is None:
        raise ValueError("遮罩是空的")
    pose_rgb = pose_rgb.crop(bbox)
    m = m.crop(bbox)

    target_h = max(1, round(placement.height * bg.height))
    target_w = max(1, round(pose_rgb.width * target_h / pose_rgb.height))
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


def largest_component(mask_png: bytes) -> bytes:
    """只保留遮罩中最大的 8 連通區塊（>128 視為前景），輸出同尺寸 L 模式 PNG（前景 255、其餘 0）。
    用來丟掉與角色分離的陰影等碎塊。全黑遮罩原樣回傳全黑。"""
    with Image.open(io.BytesIO(mask_png)) as im:
        arr = np.asarray(im.convert("L"))
    binary = (arr > 128).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out = np.zeros_like(binary)
    if n > 1:
        best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        out[labels == best] = 255
    buf = io.BytesIO()
    Image.fromarray(out, "L").save(buf, "PNG")
    return buf.getvalue()
