"""確定性合成：把去背的角色動作圖貼到背景圖的指定位置（純 Pillow／numpy，不需 GPU）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

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
        return cls(
            cx=float(d["cx"]),
            cy=float(d["cy"]),
            height=float(d["height"]),
            flip=bool(d.get("flip", False)),
        )

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


def compose(
    background: Image.Image, pose: Image.Image, mask: Image.Image, placement: Placement
) -> Image.Image:
    """回傳與背景同尺寸的 RGB 合成圖。超出背景的部分會被裁掉。"""
    bg = background.convert("RGB")
    pose_rgb = pose.convert("RGB")
    m = mask.convert("L")
    bbox = Image.fromarray((np.array(m) > 128).astype(np.uint8) * 255).getbbox()
    if bbox is None:
        raise ValueError("遮罩是空的")
    pose_rgb = pose_rgb.crop(bbox)
    m = m.crop(bbox)

    target_h = max(1, round(placement.height * bg.height))
    target_w = max(1, round(pose_rgb.width * target_h / pose_rgb.height))
    pose_rgb = pose_rgb.resize((target_w, target_h), Image.LANCZOS)
    m = m.resize((target_w, target_h), Image.LANCZOS)
    if placement.flip:
        pose_rgb = ImageOps.mirror(pose_rgb)
        m = ImageOps.mirror(m)
    m = m.filter(ImageFilter.GaussianBlur(1))

    x = round(placement.cx * bg.width - target_w / 2)
    y = round(placement.cy * bg.height - target_h / 2)
    out = bg.copy()
    out.paste(pose_rgb, (x, y), m)  # paste 會自動裁掉超出邊界的部分
    return out
