from app.services.prompts import PAGE_STYLE, character_prompt, page_prompt


def test_page_prompt_joins_parts():
    assert page_prompt(" a bunny ", "in a park") == "a bunny, in a park, " + PAGE_STYLE


def test_page_prompt_empty_scene():
    assert page_prompt("a bunny", "  ") == "a bunny, " + PAGE_STYLE


def test_character_prompt_contains_description_and_white_background():
    p = character_prompt("  a red fox  ")
    assert "a red fox" in p and "  a red fox" not in p
    assert "(isolated on pure white background:1.3)" in p
    assert "full body" in p and "character reference" not in p


def test_character_negative_suppresses_backgrounds_and_sheets():
    from app.services.prompts import CHARACTER_NEGATIVE

    for term in ("colored background", "scenery", "character sheet", "upper body"):
        assert term in CHARACTER_NEGATIVE


def test_background_prompt_has_scene_and_no_people():
    from app.services.prompts import BACKGROUND_NEGATIVE, background_prompt

    p = background_prompt("a park")
    assert p.startswith("a park, ") and "no people" in p
    assert "(a few large simple objects:1.3)" in p
    assert "people" in BACKGROUND_NEGATIVE and "cluttered" in BACKGROUND_NEGATIVE
    # 場景描述空白時不能以逗號開頭
    assert not background_prompt("   ").startswith(",")


def test_pose_prompt_has_character_and_action_but_no_scene():
    from app.services.prompts import POSE_NEGATIVE, POSE_SIZE, pose_prompt

    p = pose_prompt("  a blue bunny ", " sitting on a swing ")
    assert "a blue bunny" in p and "sitting on a swing" in p
    assert "white background" in p and "full body" in p
    assert "park" not in p and "scene" not in p
    assert "scenery" in POSE_NEGATIVE
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
    assert pick_variation(random.Random(1)) is None


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
