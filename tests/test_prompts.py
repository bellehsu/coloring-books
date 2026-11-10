import pytest

from app.services.prompts import PAGE_STYLE, page_prompt


def test_page_prompt_joins_parts():
    assert page_prompt(" a bunny ", "in a park") == "a bunny, in a park, " + PAGE_STYLE


def test_page_prompt_empty_scene():
    assert page_prompt("a bunny", "  ") == "a bunny, " + PAGE_STYLE


def test_pose_size():
    from app.services.prompts import POSE_SIZE

    assert POSE_SIZE == (832, 1216)


def test_qwen_pose_prompt():
    import random

    from app.services import prompts
    from app.services.prompts import pick_variation, qwen_pose_prompt

    p = qwen_pose_prompt("  waving one hand ")
    assert p.startswith("Make this exact character waving one hand.")
    assert "no shadow" in p and "plain pure white background" in p
    assert "{action}" not in p
    v = qwen_pose_prompt("running", "facing slightly to the left")
    assert "running, facing slightly to the left." in v
    assert prompts.QWEN_POSE_VARIATIONS == ()
    assert pick_variation(prompts.QWEN_POSE_VARIATIONS, random.Random(1)) is None
    assert prompts.pick_pose_variation(random.Random(1)) is None
    # 中文動作原樣放進英文樣板（不翻譯）
    assert qwen_pose_prompt("揮手").startswith("Make this exact character 揮手.")


QWEN_POSE_T1 = (
    "Make this exact character {action}. Draw only the character, even if the action mentions a place: no ground, "
    "no floor, no scenery, no background objects. Keep the same character design, proportions, colors, clothing and "
    "accessories unchanged. Full body, centered, plain pure white background, no shadow, no cast shadow, children's "
    "picture book illustration, flat colors, thick clean black outlines."
)


def test_pose_template_is_task1_generic_wording():
    # phase9 Task 1：拿掉兔子專用的「scarf and ears」，並在動作提到地點時也只畫角色
    from app.services.prompts import QWEN_POSE_TEMPLATE

    assert QWEN_POSE_TEMPLATE == QWEN_POSE_T1
    t = QWEN_POSE_TEMPLATE.lower()
    assert "scarf" not in t and "ears" not in t


QA1_TAIL = ("Simple coloring book background, a few large simple objects, lots of empty space, children's "
            "picture book illustration, flat colors, thick clean black outlines, simple shapes. Empty scene, "
            "no people, no animals, no characters, no text.")


def test_qwen_background_prompt():
    from app.services.prompts import qwen_background_prompt

    assert qwen_background_prompt("in a park") == f"in a park. {QA1_TAIL}"
    assert qwen_background_prompt("  in a park ") == f"in a park. {QA1_TAIL}"
    v = qwen_background_prompt("in a park", variation="wide view")
    assert "in a park. wide view. Simple coloring book background" in v
    assert v == f"in a park. wide view. {QA1_TAIL}"


def test_pick_background_variation_empty_returns_none(monkeypatch):
    import random

    from app.services import prompts

    monkeypatch.setattr(prompts, "QWEN_BACKGROUND_VARIATIONS", ())
    assert prompts.pick_background_variation(random.Random(1)) is None


def test_background_variations_calibrated():
    # phase8_qwen_background_variation_20261110：VB（主體位置）通過；VA 過小區域、VC 缺口超標
    from app.services.prompts import QWEN_BACKGROUND_VARIATIONS

    assert QWEN_BACKGROUND_VARIATIONS == (
        "The main objects are on the left side",
        "The main objects are on the right side",
        "The main objects are in the middle, seen from a low angle",
        "The main objects are in the back, open space in front",
    )
    assert all(v.strip() for v in QWEN_BACKGROUND_VARIATIONS)


def test_pick_background_variation_uses_list(monkeypatch):
    import random

    from app.services import prompts

    monkeypatch.setattr(prompts, "QWEN_BACKGROUND_VARIATIONS", ("a", "b"))
    assert prompts.pick_background_variation(random.Random(1)) in ("a", "b")


def test_pick_background_variation_excludes_previous(monkeypatch):
    # 「再產生一張」：排除上一張的構圖變化語，避免連續兩張構圖幾乎一樣
    import random

    from app.services import prompts

    monkeypatch.setattr(prompts, "QWEN_BACKGROUND_VARIATIONS", ("a", "b", "c"))
    for seed in range(50):
        assert prompts.pick_background_variation(random.Random(seed), exclude="a") in ("b", "c")
    # 不在清單裡（例如清單改過）→ 從整份清單挑
    picks = {prompts.pick_background_variation(random.Random(s), exclude="zzz") for s in range(50)}
    assert picks == {"a", "b", "c"}
    picks = {prompts.pick_background_variation(random.Random(s), exclude=None) for s in range(50)}
    assert picks == {"a", "b", "c"}


def test_pick_background_variation_exclude_everything_falls_back(monkeypatch):
    import random

    from app.services import prompts

    monkeypatch.setattr(prompts, "QWEN_BACKGROUND_VARIATIONS", ("a",))
    assert prompts.pick_background_variation(random.Random(1), exclude="a") == "a"
    monkeypatch.setattr(prompts, "QWEN_BACKGROUND_VARIATIONS", ())
    assert prompts.pick_background_variation(random.Random(1), exclude="a") is None


# ---- 角色參考圖（qwen_character；phase9 Task 1 校正）----
QCHAR_T1 = (
    "Single full body front view of {description}, standing. {variation}Children's picture book illustration, "
    "flat colors, thick clean black outlines, simple shapes. Plain pure white background, no ground, no shadow, "
    "centered, whole body visible from head to feet. Only one character, no other characters, no character sheet, "
    "no multiple views, no text."
)


def test_qwen_character_constants_from_task1():
    from app.services import prompts

    assert prompts.QWEN_CHARACTER_TEMPLATE == QCHAR_T1
    assert prompts.QWEN_CHARACTER_VARIATIONS == (
        "Big head and small body, gentle smile",
        "Slightly taller and slimmer proportions, big happy smile",
        "Chubby round shape, cheerful laughing expression",
        "Turned slightly to one side, playful happy expression",
    )
    assert prompts.QWEN_CHARACTER_SIZE == (832, 1216)
    assert all(v % 16 == 0 for v in prompts.QWEN_CHARACTER_SIZE)


def test_qwen_character_prompt_inserts_description():
    from app.services.prompts import qwen_character_prompt

    # 中文描述原樣放進英文樣板（不翻譯），前後空白去掉
    p = qwen_character_prompt("  一隻圓圓的小白兔，戴紅色圍巾 ")
    assert "一隻圓圓的小白兔，戴紅色圍巾" in p and " 一隻圓圓的小白兔，戴紅色圍巾 " not in p
    assert p == QCHAR_T1.format(description="一隻圓圓的小白兔，戴紅色圍巾", variation="")
    assert "{" not in p
    v = qwen_character_prompt("a red fox", "  Chubby round shape, cheerful laughing expression ")
    assert v == QCHAR_T1.format(description="a red fox",
                                variation="Chubby round shape, cheerful laughing expression. ")
    assert "standing. Chubby round shape, cheerful laughing expression. Children's" in v
    assert qwen_character_prompt("a red fox", "   ") == QCHAR_T1.format(description="a red fox", variation="")
    assert qwen_character_prompt("a red fox", None) == QCHAR_T1.format(description="a red fox", variation="")


def test_pick_character_variation(monkeypatch):
    import random

    from app.services import prompts

    picks = {prompts.pick_character_variation(random.Random(s)) for s in range(80)}
    assert picks == set(prompts.QWEN_CHARACTER_VARIATIONS)
    monkeypatch.setattr(prompts, "QWEN_CHARACTER_VARIATIONS", ("a", "b", "c"))
    for seed in range(50):  # 「再產生一張」：不重複上一張的變化語
        assert prompts.pick_character_variation(random.Random(seed), exclude="a") in ("b", "c")
    monkeypatch.setattr(prompts, "QWEN_CHARACTER_VARIATIONS", ("a",))
    assert prompts.pick_character_variation(random.Random(1), exclude="a") == "a"
    monkeypatch.setattr(prompts, "QWEN_CHARACTER_VARIATIONS", ())
    assert prompts.pick_character_variation(random.Random(1)) is None
    assert prompts.pick_character_variation(random.Random(1), exclude="a") is None


@pytest.mark.parametrize("options", [(), ("a",), ("a", "b", "c")])
def test_pick_variation_shared_helper(options):
    import random

    from app.services.prompts import pick_variation

    if not options:
        assert pick_variation(options, random.Random(1)) is None
        assert pick_variation(options, random.Random(1), exclude="a") is None
        return
    if len(options) == 1:  # 排除後沒得挑 → 整份清單
        assert pick_variation(options, random.Random(1), exclude="a") == "a"
        return
    for s in range(50):
        assert pick_variation(options, random.Random(s), exclude="b") in ("a", "c")
    assert {pick_variation(options, random.Random(s), exclude="zzz") for s in range(50)} == set(options)
    assert {pick_variation(options, random.Random(s)) for s in range(50)} == set(options)
