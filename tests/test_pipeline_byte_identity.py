"""線稿與列印頁的位元組不變釘子（結構重構的保險）：用固定的圖算出 SHA-256，常數取自重構前的程式。

任何一個雜湊變了，代表線稿演算法或輸出變了：要嘛還原，要嘛有意改演算法（RENDER_VERSION +1）並更新這裡。
"""

from __future__ import annotations

import hashlib
import io
import json

import numpy as np
import pytest
from PIL import Image

from app.services import lineart_post
from app.services.compose import Placement, compose
from app.services.export import layout_print_page
from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.imageio import ink_to_png
from app.services.lineart_render import LineDetail, render_base, render_lineart
from app.services.qa import evaluate
from app.storage import load_rgb
from tests.conftest import REPO

FONT = REPO / "assets" / "fonts" / "NotoSansTC-VF.ttf"
SAMPLE = REPO / "experiments" / "samples" / "character_ref.png"

# 以 ink（True＝黑）算雜湊：形狀＋壓縮成位元組
EXPECTED = {
    "render_standard_a4home": "9930009b85b118b249e3bb27cfe9910c05f5fb9d9558dc4c8ea50b45bf75aaf4",
    "render_simple_w8": "dc0cebd6f1684da829d3678c670797a0ca5d8e1e7db978e4aebae25608ac6e25",
    "render_detailed_w16": "4f2ec73b04841c2e1b3e4f05e844bd803f7d683e97c802a26631dfc408cfbfec",
    "render_rgba_flattened": "2d4bf3102cf5af4e637fda2f999cf8d7f22d7154b0b543b353b2873cde7bde77",
    "ink_to_png_bytes": "ffc5d0ba55b8680bbd3e8395c0a6a1f64754d14fd297588af057604e66d972cb",
    "print_page_home": "5618edced2d8e57fe1ef761a9a5f285807ea8548a8a776f1010a98a6bf585dfc",
    "print_page_shop_captions": "bfa01dcc16da413cc89ce31fa1368bda42ec5173bd667665eeef0e20f938c4fa",
    "qa_report": "d06bc520eef02159c2c88b4bccaa763294cf399fd6b50ad8a5d773220271a79c",
    "compose_rgba_bg": "a07290df1f551e90bc9aa7c57d7f741b107fb6893c2d3bcf32e9b752f1da6a12",
    "storage_rgba_flatten": "2a4bb456b90a10a8f2778152775911b9ce9dde13a0111c6f01c4e46403569fa6",
}


def h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def h_ink(ink: np.ndarray) -> str:
    return h(str(ink.shape).encode() + np.packbits(ink.astype(bool)).tobytes())


def synthetic_color(w: int = 400, h_: int = 560) -> np.ndarray:
    """固定的彩色插畫：漸層底、圓、矩形、斜線（沒有隨機數）。"""
    yy, xx = np.mgrid[0:h_, 0:w]
    img = np.stack([(xx * 255 // w), (yy * 255 // h_), ((xx + yy) * 255 // (w + h_))], -1).astype(np.uint8)
    cx, cy = w // 2, h_ // 2
    img[((xx - cx) ** 2 + (yy - cy) ** 2) < 120 ** 2] = (240, 200, 30)
    img[(abs(xx - cx) < 90) & (abs(yy - cy) < 20)] = (20, 20, 20)
    img[(abs(xx - yy) < 4) & (xx > 50)] = (10, 10, 120)
    img[40:80, 40:200] = (255, 255, 255)
    return img


def synthetic_rgba_png() -> bytes:
    rgb = synthetic_color(300, 420)
    alpha = np.full(rgb.shape[:2], 255, np.uint8)
    alpha[:100, :] = 0
    alpha[200:260, 100:200] = 120
    buf = io.BytesIO()
    Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(buf, "PNG")
    return buf.getvalue()


def cases() -> dict[str, str]:
    out: dict[str, str] = {}
    a4 = compute_layout(PaperSize.A4, PrintMode.HOME)
    sample = lineart_post.load_image(SAMPLE)
    ink = render_base(sample, a4.content_px, LineDetail.STANDARD, 11)[0]
    out["render_standard_a4home"] = h_ink(ink)
    out["ink_to_png_bytes"] = h(ink_to_png(ink))
    out["qa_report"] = h(json.dumps(evaluate(ink, 11).to_dict(), sort_keys=True).encode())
    syn = synthetic_color()
    out["render_simple_w8"] = h_ink(render_base(syn, (620, 800), LineDetail.SIMPLE, 8)[0])
    out["render_detailed_w16"] = h_ink(render_base(syn, (700, 700), LineDetail.DETAILED, 16)[0])
    flat = lineart_post.load_image(io.BytesIO(synthetic_rgba_png()))
    out["render_rgba_flattened"] = h_ink(render_base(flat, (500, 700), LineDetail.STANDARD, 11)[0])
    # 列印頁（無字、有字）
    out["print_page_home"] = h(layout_print_page(ink, a4).tobytes())
    shop = compute_layout(PaperSize.A5, PrintMode.PRINT_SHOP)
    small = render_base(syn, shop.content_px, LineDetail.STANDARD, 11)[0]
    out["print_page_shop_captions"] = h(layout_print_page(small, shop, ("消防車", "Fire Engine"), FONT).tobytes())
    # 合成（RGBA 背景）與上傳壓平
    bg = Image.open(io.BytesIO(synthetic_rgba_png()))
    pose = Image.fromarray(synthetic_color(200, 300), "RGB")
    mask = Image.new("L", pose.size, 0)
    mask.paste(255, (20, 20, 180, 280))
    out["compose_rgba_bg"] = h(compose(bg, pose, mask, Placement(0.5, 0.6, 0.5, True)).tobytes())
    out["storage_rgba_flatten"] = h(np.asarray(load_rgb(synthetic_rgba_png())[0]).tobytes())
    return out


def product_cases() -> dict[str, str]:
    """產品實際走的 render_lineart（不算指標）要和 render_base 的雜湊一樣。"""
    a4 = compute_layout(PaperSize.A4, PrintMode.HOME)
    syn = synthetic_color()
    flat = lineart_post.load_image(io.BytesIO(synthetic_rgba_png()))
    return {
        "render_standard_a4home": h_ink(render_lineart(lineart_post.load_image(SAMPLE), a4.content_px,
                                                       LineDetail.STANDARD, 11)),
        "render_simple_w8": h_ink(render_lineart(syn, (620, 800), LineDetail.SIMPLE, 8)),
        "render_detailed_w16": h_ink(render_lineart(syn, (700, 700), LineDetail.DETAILED, 16)),
        "render_rgba_flattened": h_ink(render_lineart(flat, (500, 700), LineDetail.STANDARD, 11)),
    }


@pytest.mark.parametrize("name", ["render_standard_a4home", "render_simple_w8", "render_detailed_w16",
                                  "render_rgba_flattened"])
def test_render_lineart_byte_identical(name):
    assert product_cases()[name] == EXPECTED[name]


@pytest.mark.parametrize("name", list(EXPECTED))
def test_byte_identical(name):
    got = _cached()[name]
    assert got == EXPECTED[name], f"{name} changed: {got}"


_CACHE: dict[str, str] = {}


def _cached() -> dict[str, str]:
    if not _CACHE:
        _CACHE.update(cases())
    return _CACHE


if __name__ == "__main__":
    print(json.dumps(cases(), indent=4))
