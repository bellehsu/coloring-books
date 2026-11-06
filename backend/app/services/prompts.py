"""提示詞組合：頁面與角色參考圖的固定樣板。"""

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
