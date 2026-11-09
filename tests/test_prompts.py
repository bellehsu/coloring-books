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
