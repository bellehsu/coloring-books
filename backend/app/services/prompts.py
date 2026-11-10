"""提示詞組合：頁面與角色參考圖的固定樣板。"""
import random

PAGE_STYLE = "children's picture book illustration, flat colors, thick clean black outlines, simple shapes, full detailed background scene"
PAGE_NEGATIVE = "plain background, empty background, white background, photo, realistic, 3d render, gradient, soft shading, airbrush, texture, noise, blurry, sketch, monochrome, grayscale, text, watermark, signature"
# 角色負向：前段壓制彩色背景、場景與多視角設定圖（Phase 3 實驗 phase3_character_prompt_20261106_154833，v4）
CHARACTER_NEGATIVE = "colored background, blue background, gray background, gradient background, scenery, landscape, ground, grass, multiple views, character sheet, reference sheet, turnaround, close-up, portrait, upper body, photo, realistic, 3d render, gradient, soft shading, airbrush, texture, noise, blurry, sketch, monochrome, grayscale, text, watermark, signature, multiple characters, cropped"


def page_prompt(character_description: str, scene: str) -> str:
    parts = [character_description.strip(), scene.strip(), PAGE_STYLE]
    return ", ".join(p for p in parts if p)


def character_prompt(description: str) -> str:
    # 不寫「character reference」：會讓模型畫成多視角設定圖。白底以 ComfyUI 權重語法加強。
    return (
        f"single full body front view of {description.strip()}, standing, "
        "children's picture book illustration, flat colors, thick clean black outlines, simple shapes, "
        "(isolated on pure white background:1.3), centered, whole body visible from head to feet"
    )


# --- 頁面合成流程（背景＋角色動作）；初始文字取自 phase5_compose_spike_20261109，Task 6 校正後可改 ---
BACKGROUND_STYLE = "children's picture book illustration, flat colors, thick clean black outlines, simple shapes"
# 背景提示詞經 Task 6 校正（experiments/results/phase5_background_prompt_20261109）：
# 加權強調「簡單、少量大物件」＋負向壓制繁複細節，缺口平均 67 → 42、過小區域約減半。
BACKGROUND_NEGATIVE = (
    "cluttered, busy, intricate details, many small objects, tiny details, hatching, crosshatching, pattern, "
    "people, person, character, girl, boy, child, animal, rabbit, photo, realistic, 3d render, gradient, "
    "soft shading, texture, noise, blurry, text, watermark"
)
POSE_NEGATIVE = "colored background, scenery, landscape, ground, multiple views, character sheet, cropped, photo, realistic, 3d render, gradient, soft shading, texture, noise, blurry, text, watermark, multiple characters"
POSE_SIZE = (832, 1216)


def background_prompt(scene: str) -> str:
    parts = [
        scene.strip(),
        "(simple coloring book background:1.2), (a few large simple objects:1.3), lots of empty space",
        BACKGROUND_STYLE,
        "no people",
    ]
    return ", ".join(p for p in parts if p)


def pose_prompt(character_description: str, action: str) -> str:
    # 不帶場景描述：背景另外生成，這裡只要白底角色
    return (
        f"{character_description.strip()}, {action.strip()}, full body, {BACKGROUND_STYLE}, "
        "(isolated on pure white background:1.3), centered"
    )


# --- 精準模式（qwen_edit_pose_cutout）：指令式編輯參考圖 ---
# 校正見 experiments/results/phase6_qwen_pose_calibration_20261110：
# 以 phase5 比較實驗的措辭（最貼近參考圖的耳朵與圍巾垂墜）只加「no shadow」最好（V6）；
# 改寫圍巾／手臂會讓坐姿失效、耳朵走樣；加朝向等隨機變化會讓一致性下降 → 不採用（VARIATIONS 為空）。
QWEN_POSE_TEMPLATE = (
    "Make this exact character {action}. Keep the same character design, proportions, colors, scarf and "
    "ears unchanged. Full body, centered, plain pure white background, no shadow, no cast shadow, "
    "children's picture book illustration, flat colors, thick clean black outlines."
)
QWEN_POSE_VARIATIONS: tuple[str, ...] = ()


def qwen_pose_prompt(action: str, variation: str | None = None) -> str:
    act = action.strip()
    if variation:
        act = f"{act}, {variation.strip()}"
    return QWEN_POSE_TEMPLATE.format(action=act)


def pick_variation(rng: random.Random) -> str | None:
    return rng.choice(QWEN_POSE_VARIATIONS) if QWEN_POSE_VARIATIONS else None
