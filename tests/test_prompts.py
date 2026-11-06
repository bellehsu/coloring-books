from app.services.prompts import PAGE_STYLE, character_prompt, page_prompt


def test_page_prompt_joins_parts():
    assert page_prompt(" a bunny ", "in a park") == "a bunny, in a park, " + PAGE_STYLE


def test_page_prompt_empty_scene():
    assert page_prompt("a bunny", "  ") == "a bunny, " + PAGE_STYLE


def test_character_prompt_contains_description_and_white_background():
    p = character_prompt("  a red fox  ")
    assert "a red fox" in p and "  a red fox" not in p
    assert "plain white background" in p
