"""不放主角的頁：把場景文字改寫成「主體在畫面中間、畫得很大」（spec 2026-11-26-no-character-pages §5.1）。
前端有同規則的副本（frontend/src/lib/noCharacterScene.ts），兩邊共用 noCharacterScene.cases.json。"""

BIG_PHRASE = "畫得很大，佔滿大半個畫面"


def no_character_scene(scene: str) -> str:
    text = scene.replace("畫面右邊", "畫面中間").replace("畫面左邊", "畫面中間").replace("小小的", "")
    parts = text.split("，")
    for i, part in enumerate(parts):
        if "畫面中間" in part:
            parts[i + 1 : i + 1] = [BIG_PHRASE]
            break
    return "，".join(parts)
