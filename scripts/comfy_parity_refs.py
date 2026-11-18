#!/usr/bin/env python3
"""產生 ComfyUI 節點包（ComfyUI-ColoringBook）的對照資料：只用網站自己的 app.services 模組算出參考輸出。

節點包的 tests/core/test_parity.py 用 coloring_core 重算，逐像素比對這裡的輸出。
用法（網站 venv）：python scripts/comfy_parity_refs.py --out <節點包>/tests/fixtures/parity
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.autofix import MAX_STROKES, MAX_TOTAL_POINTS, plan_autofix  # noqa: E402
from app.services.compose import DEFAULT_PLACEMENT, Placement, compose, largest_component  # noqa: E402
from app.services.export import layout_print_page, png_bytes  # noqa: E402
from app.services.imageio import flatten_on_white, ink_to_png, png_to_ink  # noqa: E402
from app.services.layout import PaperSize, PrintMode, compute_layout  # noqa: E402
from app.services.lineart_render import LineDetail, apply_strokes, render_lineart  # noqa: E402
from app.services.prompts import (  # noqa: E402
    qwen_background_prompt, qwen_character_prompt, qwen_pose_prompt,
)
from app.services.qa import evaluate  # noqa: E402
from app.services.uploads import cover_crop  # noqa: E402

INPUT_DIR = ROOT / "experiments/results/phase14_single_outline_20261113_1009/input"
FONT = ROOT / "assets/fonts/NotoSansTC-VF.ttf"
CAPTIONS = ("小兔子去野餐", "The bunny goes on a picnic")
BG_COVER_SIZE = (832, 1216)

# 提示詞輸入：(說明文字, 變化語)。表情與面向的片語取自 frontend/src/lib/actions.ts 的 EXPRESSIONS／FACINGS，
# 這裡只有動作文字加上兩個片語（輸入沒有已存在的片語，所以就是「, 」串接）。
EXPR_FACING = [
    ("開心", "with a happy smiling expression", "朝左", "facing toward the left side of the picture"),
    ("害羞", "with a shy blushing expression", "正面", "facing the viewer"),
]
CHARACTER_INPUTS = [("a small white bunny wearing a red scarf", None), ("一隻戴紅圍巾的小白兔", "Chubby round shape, cheerful laughing expression")]
POSE_INPUTS = [("jumping happily", None), ("在草地上跳舞", "with a calmer, smaller motion")]
BACKGROUND_INPUTS = [("a sunny meadow with a big tree", None), ("森林裡的小木屋", "The main objects are on the left side")]


def _save_ink(ink: np.ndarray, path: Path) -> None:
    path.write_bytes(ink_to_png(ink))


def _arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    out: Path = ap.parse_args().out
    out.mkdir(parents=True, exist_ok=True)

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()

    # 輸入：p01 角色、p02 背景（原檔複製），遮罩＝p01 非白像素
    p01 = Image.open(INPUT_DIR / "p01.png").convert("RGB")
    p02 = Image.open(INPUT_DIR / "p02.png").convert("RGB")
    p01.save(out / "p01.png")
    p02.save(out / "p02.png")
    mask = Image.fromarray(((np.asarray(p01) < 245).any(axis=2) * 255).astype(np.uint8), "L")
    mask.save(out / "mask.png")

    clean_bytes = largest_component((out / "mask.png").read_bytes())
    (out / "mask_clean.png").write_bytes(clean_bytes)
    mask_clean = Image.open(out / "mask_clean.png").convert("L")

    # p01 RGBA（alpha＝mask_clean，四角透明）給 bg_cover
    rgba = p01.convert("RGBA")
    rgba.putalpha(mask_clean)
    rgba.save(out / "p01_rgba.png")

    cases: list[dict] = []

    def case(name, inputs, params, outputs):
        cases.append({"name": name, "inputs": inputs, "params": params, "outputs": outputs})

    case("mask_clean", {"mask": "mask.png"}, {}, {"mask": "mask_clean.png"})

    # 合成
    comp_inputs = {"background": "p02.png", "character": "p01.png", "mask": "mask_clean.png"}
    default_pl = {"cx": DEFAULT_PLACEMENT.cx, "cy": DEFAULT_PLACEMENT.cy,
                  "height": DEFAULT_PLACEMENT.height, "flip": DEFAULT_PLACEMENT.flip}
    flip_pl = {"cx": 0.3, "cy": 0.7, "height": 0.8, "flip": True}
    comps = {}
    for name, pl in (("composite", default_pl), ("composite_flip", flip_pl)):
        img = compose(p02, p01, mask_clean, Placement(**pl))
        img.save(out / f"{name}.png")
        comps[name] = img
        case(name, comp_inputs, {"placement": pl}, {"rgb": f"{name}.png"})

    # 線稿
    a4 = compute_layout(PaperSize.A4, PrintMode.HOME)
    letter = compute_layout(PaperSize.LETTER, PrintMode.HOME)
    color = _arr(comps["composite"])
    ink_cases = [
        ("ink_a4", a4, PaperSize.A4, LineDetail.STANDARD, 11, True),
        ("ink_a4_nofill", a4, PaperSize.A4, LineDetail.STANDARD, 11, False),
        ("ink_letter_detailed", letter, PaperSize.LETTER, LineDetail.DETAILED, 14, True),
    ]
    inks = {}
    for name, lay, paper, detail, width, fill in ink_cases:
        ink = render_lineart(color, lay.content_px, detail, width, outline_fill=fill)
        _save_ink(ink, out / f"{name}.png")
        inks[name] = ink
        case(name, {"image": "composite.png"},
             {"paper": paper.value, "print_mode": "home", "content_px": list(lay.content_px),
              "detail": detail.value, "line_width_px": width, "outline_fill": fill},
             {"ink": f"{name}.png"})

    # 補線：網站 pipeline 步驟 3–4（沒有 user 層）
    pre = inks["ink_a4"]
    qa = evaluate(pre, 11)
    auto = (plan_autofix(pre, [], qa.gap_count, qa.bridges, 11,
                         room_strokes=MAX_STROKES, room_points=MAX_TOTAL_POINTS) if qa.bridges else [])
    fixed = apply_strokes(pre, auto) if auto else pre
    _save_ink(fixed, out / "ink_a4_fixed.png")
    case("ink_a4_fixed", {"ink": "ink_a4.png"}, {"line_width_px": 11},
         {"ink": "ink_a4_fixed.png", "gap_count_before": int(qa.gap_count), "strokes_added": len(auto),
          "changed": bool((fixed != pre).any())})

    # 列印頁
    fixed_ink = png_to_ink((out / "ink_a4_fixed.png").read_bytes())
    pages = [
        ("page_a4_both", CAPTIONS, (True, True)),
        ("page_a4_blank", CAPTIONS, (False, False)),
        ("page_a4_none", (None, None), (True, True)),
    ]
    for name, caps, show in pages:
        page = layout_print_page(fixed_ink, a4, caps, FONT, show)
        (out / f"{name}.png").write_bytes(png_bytes(page))
        case(name, {"ink": "ink_a4_fixed.png"},
             {"paper": "a4", "print_mode": "home", "captions": list(caps), "show": list(show),
              "font": "fonts/NotoSansTC-VF.ttf"},
             {"page": f"{name}.png"})

    # 背景裁切
    bg, box = cover_crop(flatten_on_white(Image.open(out / "p01_rgba.png")), BG_COVER_SIZE)
    bg.save(out / "bg_cover.png")
    case("bg_cover", {"image": "p01_rgba.png"}, {"size": list(BG_COVER_SIZE)},
         {"rgb": "bg_cover.png", "box": list(box)})

    # 提示詞
    prompts = []
    for kind, fn, inputs in (("character", qwen_character_prompt, CHARACTER_INPUTS),
                             ("pose", qwen_pose_prompt, POSE_INPUTS),
                             ("background", qwen_background_prompt, BACKGROUND_INPUTS)):
        for i, (text, variation) in enumerate(inputs):
            _, expr, _, facing = EXPR_FACING[i]
            base, phrases = text, []
            if kind == "pose":
                phrases = [expr, facing]
            elif kind == "character":
                phrases = [expr]
            text = ", ".join([base, *phrases])  # 同 actions.ts appendPhrase（輸入不含這些片語）
            prompts.append({"template": kind, "base": base, "phrases": phrases, "text": text,
                            "variation": variation, "prompt": fn(text, variation)})
    (out / "prompts.json").write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
    case("prompts", {}, {}, {"prompts": "prompts.json"})

    (out / "manifest.json").write_text(
        json.dumps({"website_commit": commit, "generator": "scripts/comfy_parity_refs.py", "cases": cases},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
