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
# 以 phase6_qwen_pose_calibration_20261110 的 V6 為底。Phase 12 改 T4（spec 2026-11-11-pose-expressions §3.1）：
# 動作提到交通工具／物品才畫那一樣（完整可見），沒提就不加別的；不畫地面與場景，no text, no letters。
QWEN_POSE_TEMPLATE = (
    "Make this exact character {action}. Draw only the character. If the action names a vehicle or an object, also "
    "draw that one thing, completely visible; if it names none, add nothing else; no ground, no floor, no road, no "
    "scenery, no room, no other background objects, no text, no letters. Keep the same character design, "
    "proportions, colors, clothing and accessories unchanged. Full body, centered, plain pure white background, no "
    "shadow, no cast shadow, children's picture book illustration, flat colors, thick clean black outlines."
)
# 再產生的變化語（spec §3.2）：每次隨機一個，排除這頁上一張動作用的；前兩個是轉向，後兩個是動作幅度。
QWEN_POSE_VARIATIONS: tuple[str, ...] = (
    "seen in a three-quarter view turned slightly to the left",
    "seen in a three-quarter view turned slightly to the right",
    "with a bigger, more energetic motion",
    "with a calmer, smaller motion",
)
# 動作文字含這些英文面向語（不分大小寫）時，只用幅度類變化語（3、4），免得轉向和使用者選的面向打架。
FACING_PHRASES: tuple[str, ...] = (
    "facing toward the left side of the picture",
    "facing the viewer",
    "facing toward the right side of the picture",
)
POSE_SIZE = (832, 1216)  # 角色動作固定尺寸（直式、16 的倍數）


def qwen_pose_prompt(action: str, variation: str | None = None) -> str:
    act = action.strip()
    if variation:
        act = f"{act}, {variation.strip()}"
    return QWEN_POSE_TEMPLATE.format(action=act)


def has_facing(action: str) -> bool:
    low = action.lower()
    return any(p in low for p in FACING_PHRASES)


def pick_pose_variation(
    rng: random.Random, exclude: str | Sequence[str] | None = None, facing: bool = False
) -> str | None:
    """exclude：這頁最近用過的變化語（字串或由新到舊的清單）。候選有 3 個以上（沒有面向語）就排除最近兩個，
    只剩 2 個（有面向語）就只排除最近一個，避免 A-B-A 或兩個輪流重複。"""
    options = QWEN_POSE_VARIATIONS[2:] if facing else QWEN_POSE_VARIATIONS
    recent = [exclude] if isinstance(exclude, str) else list(exclude or [])
    keep = 2 if len(options) >= 3 else 1
    choices = [v for v in options if v not in recent[:keep]] or list(options)
    return rng.choice(choices)


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
