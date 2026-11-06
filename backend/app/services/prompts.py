"""提示詞組合：頁面與角色參考圖的固定樣板。"""

PAGE_STYLE = "children's picture book illustration, flat colors, thick clean black outlines, simple shapes, full detailed background scene"
PAGE_NEGATIVE = "plain background, empty background, white background, photo, realistic, 3d render, gradient, soft shading, airbrush, texture, noise, blurry, sketch, monochrome, grayscale, text, watermark, signature"
CHARACTER_NEGATIVE = "photo, realistic, 3d render, gradient, soft shading, airbrush, texture, noise, blurry, sketch, monochrome, grayscale, text, watermark, signature, multiple characters, cropped"


def page_prompt(character_description: str, scene: str) -> str:
    parts = [character_description.strip(), scene.strip(), PAGE_STYLE]
    return ", ".join(p for p in parts if p)


def character_prompt(description: str) -> str:
    return (
        f"character reference, single full body front view of {description.strip()}, standing, "
        "children's picture book illustration, flat colors, thick clean black outlines, simple shapes, "
        "plain white background, centered, whole body visible"
    )
