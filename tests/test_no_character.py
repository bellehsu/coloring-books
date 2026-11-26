"""不放主角：場景文字改寫規則，與前端共用 frontend/src/lib/noCharacterScene.cases.json。"""
import json
from pathlib import Path

from app.services.no_character import no_character_scene

CASES = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "noCharacterScene.cases.json"


def test_shared_cases():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(cases) >= 8
    for c in cases:
        assert no_character_scene(c["in"]) == c["out"], c["in"]
