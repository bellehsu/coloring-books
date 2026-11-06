from pathlib import Path

import pytest

from app.models import GenKind
from app.services.comfy_engine import (
    CHARACTER_WORKFLOW, PAGE_FIXED, PAGE_WORKFLOW, ComfyEngine, page_params,
)
from app.services.engine import EngineError, EngineUnavailable, GenerationRequest
from app.services.workflow_registry import WorkflowRegistry
from tests.conftest import REPO
from tests.fake_comfy import FakeComfy, png_bytes


def make_req(tmp_path: Path, kind=GenKind.PAGE, character=True, structure=False, strength=0.4, **kw):
    char = None
    if character:
        char = tmp_path / "char.png"
        char.write_bytes(png_bytes())
    struct = None
    if structure:
        struct = tmp_path / "struct.png"
        struct.write_bytes(png_bytes())
    return GenerationRequest(kind=kind, prompt="a cat", negative_prompt="color", seed=7, width=640, height=896,
                             character_image=char, structure_image=struct, structure_strength=strength, **kw)


def make_engine(fake: FakeComfy) -> ComfyEngine:
    registry = WorkflowRegistry(REPO / "comfy_workflows").load()
    return ComfyEngine(lambda: fake.client(), registry, run_timeout_s=10)


def by_title(prompt: dict, title: str) -> dict:
    return next(n for n in prompt.values() if n["_meta"]["title"] == title)["inputs"]


def test_page_params_uses_spec_defaults(tmp_path):
    p = page_params(make_req(tmp_path, structure=False, strength=0.4), "c.png", "w.png")
    assert p["controlnet_strength"] == 0.0
    assert p["character_image"] == "c.png" and p["structure_image"] == "w.png"
    p = page_params(make_req(tmp_path, structure=True, strength=0.4), "c.png", "w.png")
    assert p["controlnet_strength"] == 0.4
    for k, v in PAGE_FIXED.items():
        assert p[k] == v
    assert p["ipadapter_weight"] == 0.55


async def test_generate_page_sends_whitelisted_inputs(tmp_path):
    fake = FakeComfy()
    eng = make_engine(fake)
    req = make_req(tmp_path)
    res = await eng.generate(req)
    assert res.workflow_name == PAGE_WORKFLOW
    sent = fake.prompts[0]["prompt"]
    assert by_title(sent, "IPADAPTER")["weight"] == 0.55
    assert by_title(sent, "IPADAPTER")["end_at"] == 0.6
    assert by_title(sent, "UNION_TYPE")["type"] == "hed/pidi/scribble/ted"
    assert by_title(sent, "LATENT")["width"] == 640 and by_title(sent, "LATENT")["height"] == 896
    assert len(fake.uploads) == 2
    await eng.generate(req)
    assert len(fake.uploads) == 2
    assert all(u["subfolder"] == "coloring_books" for u in fake.uploads)


async def test_generate_character_uses_text2img(tmp_path):
    seen = []
    fake = FakeComfy()
    res = await make_engine(fake).generate(
        make_req(tmp_path, kind=GenKind.CHARACTER, character=False), on_progress=lambda v, m: seen.append((v, m)))
    assert res.workflow_name == CHARACTER_WORKFLOW == "sdxl_text2img"
    assert res.models == ["sd_xl_base_1.0.safetensors"]
    assert res.image.startswith(b"\x89PNG")
    assert res.engine == "comfyui" and res.params["seed"] == 7
    assert (1, 2) in seen and (2, 2) in seen
    assert fake.uploads == []


async def test_engine_errors_mapped(tmp_path):
    fake = FakeComfy()
    eng = make_engine(fake)
    fake.dead = True
    with pytest.raises(EngineUnavailable):
        await eng.generate(make_req(tmp_path, kind=GenKind.CHARACTER, character=False))
    fake.dead = False
    fake.mode = "error"
    with pytest.raises(EngineError) as ei:
        await eng.generate(make_req(tmp_path, kind=GenKind.CHARACTER, character=False))
    assert not isinstance(ei.value, EngineUnavailable)
    assert "CUDA out of memory" in str(ei.value)
    with pytest.raises(EngineError, match="角色參考圖"):
        await eng.generate(make_req(tmp_path, character=False))


def test_text2img_manifest_loads():
    entry = WorkflowRegistry(REPO / "comfy_workflows").load().get("sdxl_text2img")
    assert entry.ok, entry.errors
