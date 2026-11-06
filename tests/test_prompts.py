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
