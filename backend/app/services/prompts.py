"""提示詞樣板。Phase 9：角色、背景、角色動作全部走 Qwen-Image-Edit-2511（qwen_character／qwen_background／
qwen_edit_pose_cutout）。Qwen 的 cfg 為 1 → 負向提示詞無效，排除條件寫在正向；生成紀錄的 negative_prompt 存空字串。
使用者輸入（角色描述、場景、動作，中文或英文）不翻譯，原樣放進英文樣板。
"""
import random
from collections.abc import Sequence

def pick_variation(options: Sequence[str], rng: random.Random, exclude: str | None = None) -> str | None:
    """從 options 隨機挑一個變化語；exclude（上一張用的）不挑，避免「再產生一張」時重複。
    排除後沒得挑（清單只有一個）就從整份清單挑；exclude 不在清單裡照常挑；清單是空的回 None。"""
    if not options:
        return None
    choices = [v for v in options if v != exclude] or list(options)
    return rng.choice(choices)


# --- 角色參考圖（qwen_character）：Qwen 純文字生圖，白底全身單一角色 ---
# 樣板、變化語與尺寸由 phase9 Task 1 在 GPU 上校正（experiments/results/phase9_all_qwen_20261110_1700）。
# {variation} 為「<變化語>. 」或空字串。
QWEN_CHARACTER_TEMPLATE = (
    "Single full body front view of {description}, standing. {variation}Children's picture book illustration, "
    "flat colors, thick clean black outlines, simple shapes. Plain pure white background, no ground, no shadow, "
    "centered, whole body visible from head to feet. Only one character, no other characters, no character sheet, "
    "no multiple views, no text."
)
# 變化語（每張隨機一個，不重複這本書上一張）：通用措辭、不提手腳，交通工具與物品類角色也適用（VG）。
QWEN_CHARACTER_VARIATIONS: tuple[str, ...] = (
    "Big head and small body, gentle smile",
    "Slightly taller and slimmer proportions, big happy smile",
    "Chubby round shape, cheerful laughing expression",
    "Turned slightly to one side, playful happy expression",
)
QWEN_CHARACTER_SIZE = (832, 1216)  # 直式、16 的倍數


def qwen_character_prompt(description: str, variation: str | None = None) -> str:
    var = f"{variation.strip()}. " if variation and variation.strip() else ""
    return QWEN_CHARACTER_TEMPLATE.format(description=description.strip(), variation=var)


def pick_character_variation(rng: random.Random, exclude: str | None = None) -> str | None:
    return pick_variation(QWEN_CHARACTER_VARIATIONS, rng, exclude)


# --- 角色動作（qwen_edit_pose_cutout）：指令式編輯參考圖 ---
# 以 phase6_qwen_pose_calibration_20261110 的 V6 為底。phase9 Task 1：原本寫死的「scarf and ears」會替沒有圍巾的
# 角色加上圍巾 → 改通用措辭；動作文字含地點時 Qwen 會畫出場景 → 加「即使動作提到地點也只畫角色」。
# 加朝向等隨機變化會讓一致性下降 → 不採用（VARIATIONS 為空）。
QWEN_POSE_TEMPLATE = (
    "Make this exact character {action}. Draw only the character, even if the action mentions a place: no ground, "
    "no floor, no scenery, no background objects. Keep the same character design, proportions, colors, clothing and "
    "accessories unchanged. Full body, centered, plain pure white background, no shadow, no cast shadow, children's "
    "picture book illustration, flat colors, thick clean black outlines."
)
QWEN_POSE_VARIATIONS: tuple[str, ...] = ()
POSE_SIZE = (832, 1216)  # 角色動作固定尺寸（直式、16 的倍數）


def qwen_pose_prompt(action: str, variation: str | None = None) -> str:
    act = action.strip()
    if variation:
        act = f"{act}, {variation.strip()}"
    return QWEN_POSE_TEMPLATE.format(action=act)


def pick_pose_variation(rng: random.Random) -> str | None:
    return pick_variation(QWEN_POSE_VARIATIONS, rng)


# --- 簡潔背景（qwen_background）：Qwen-Image-Edit-2511 當純文字生圖 ---
# 措辭為 experiments/results/phase7_qwen_background_20261110_1104 的 QA1。
# cfg 1 → 負向提示詞無效，排除條件寫在正向；生成紀錄的 negative_prompt 存空字串。
QWEN_BACKGROUND_TEMPLATE = (
    "{scene}. {variation}Simple coloring book background, a few large simple objects, lots of empty space, "
    "children's picture book illustration, flat colors, thick clean black outlines, simple shapes. "
    "Empty scene, no people, no animals, no characters, no text."
)
# 構圖變化語（「再產生一張」用，每張隨機一個）：experiments/results/phase8_qwen_background_variation_20261110
# 校正通過的 VB（主體位置）：缺口 9.8、過小區域 73.2（基準 9.5／82.3），同場景兩張構圖明顯不同。
QWEN_BACKGROUND_VARIATIONS: tuple[str, ...] = (
    "The main objects are on the left side",
    "The main objects are on the right side",
    "The main objects are in the middle, seen from a low angle",
    "The main objects are in the back, open space in front",
)


def qwen_background_prompt(scene: str, variation: str | None = None) -> str:
    var = f"{variation.strip()}. " if variation and variation.strip() else ""
    return QWEN_BACKGROUND_TEMPLATE.format(scene=scene.strip(), variation=var)


def pick_background_variation(rng: random.Random, exclude: str | None = None) -> str | None:
    """exclude 為同頁上一張簡潔背景用的變化語，避免再產生時構圖重複。"""
    return pick_variation(QWEN_BACKGROUND_VARIATIONS, rng, exclude)
