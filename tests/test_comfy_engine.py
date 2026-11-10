from pathlib import Path

import pytest

from app.models import GenKind
from app.services.comfy_engine import (
    BACKGROUND_WORKFLOW, CHARACTER_WORKFLOW, PAGE_FIXED, POSE_WORKFLOWS, PAGE_WORKFLOW, ComfyEngine, page_params,
)
from app.services.engine import EngineError, EngineUnavailable, GenerationRequest
from app.services.workflow_registry import WorkflowRegistry
from tests.conftest import REPO
from tests.fake_comfy import FakeComfy, png_bytes


def make_req(tmp_path: Path, kind=GenKind.PAGE, character=True, structure=False, strength=0.4,
             width=640, height=896, **kw):
    char = None
    if character:
        char = tmp_path / "char.png"
        char.write_bytes(png_bytes())
    struct = None
    if structure:
        struct = tmp_path / "struct.png"
        struct.write_bytes(png_bytes())
    return GenerationRequest(kind=kind, prompt="a cat", negative_prompt="color", seed=7, width=width, height=height,
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


async def test_background_with_sketch_uses_page_strength(tmp_path):
    fake = FakeComfy()
    res = await make_engine(fake).generate(
        make_req(tmp_path, kind=GenKind.BACKGROUND, character=False, structure=True, strength=0.35))
    assert res.workflow_name == BACKGROUND_WORKFLOW == "sdxl_background"
    sent = fake.prompts[0]["prompt"]
    assert by_title(sent, "APPLY_CONTROLNET")["strength"] == 0.35
    assert by_title(sent, "APPLY_CONTROLNET")["end_percent"] == 0.5
    assert by_title(sent, "UNION_TYPE")["type"] == "hed/pidi/scribble/ted"
    assert by_title(sent, "LATENT")["width"] == 640 and by_title(sent, "LATENT")["height"] == 896
    assert len(fake.uploads) == 1
    assert res.extra_images == {}
    assert not any("IPAdapter" in n["class_type"] for n in sent.values())


async def test_background_without_sketch_uses_white_and_zero_strength(tmp_path):
    fake = FakeComfy()
    await make_engine(fake).generate(
        make_req(tmp_path, kind=GenKind.BACKGROUND, character=False, structure=False, strength=0.4))
    sent = fake.prompts[0]["prompt"]
    assert by_title(sent, "APPLY_CONTROLNET")["strength"] == 0.0
    assert len(fake.uploads) == 1


async def test_pose_sends_ip_params_and_downloads_both_outputs(tmp_path):
    fake = FakeComfy()
    res = await make_engine(fake).generate(
        make_req(tmp_path, kind=GenKind.POSE, structure=False))
    assert res.workflow_name == POSE_WORKFLOWS["fast"] == "sdxl_pose_cutout"
    sent = fake.prompts[0]["prompt"]
    assert by_title(sent, "IPADAPTER")["weight"] == 0.55
    assert by_title(sent, "IPADAPTER")["end_at"] == 0.6
    assert by_title(sent, "LATENT")["width"] == 640 and by_title(sent, "LATENT")["height"] == 896
    assert len(fake.uploads) == 1  # 只有角色參考圖
    assert res.image == fake.image
    assert res.extra_images == {"mask": fake.mask_image}


async def test_pose_without_character_image_errors(tmp_path):
    with pytest.raises(EngineError, match="角色動作需要角色參考圖"):
        await make_engine(FakeComfy()).generate(make_req(tmp_path, kind=GenKind.POSE, character=False))


async def test_pose_missing_mask_output_errors(tmp_path):
    fake = FakeComfy()
    fake.skip_output_titles = {"MASK"}
    with pytest.raises(EngineError, match="去背失敗：沒有遮罩輸出"):
        await make_engine(fake).generate(make_req(tmp_path, kind=GenKind.POSE))


def test_pose_workflows_by_mode():
    assert POSE_WORKFLOWS == {"precise": "qwen_edit_pose_cutout", "fast": "sdxl_pose_cutout"}


def test_pose_request_defaults_to_fast(tmp_path):
    # 舊的（Phase 5）request_json 沒有 pose_mode → 快速
    assert make_req(tmp_path, kind=GenKind.POSE).pose_mode == "fast"


async def test_fast_pose_mode_uses_sdxl(tmp_path):
    fake = FakeComfy()
    res = await make_engine(fake).generate(make_req(tmp_path, kind=GenKind.POSE, pose_mode="fast"))
    assert res.workflow_name == "sdxl_pose_cutout"
    assert by_title(fake.prompts[0]["prompt"], "IPADAPTER")["weight"] == 0.55


async def test_precise_pose_uses_qwen_with_character_ref(tmp_path):
    fake = FakeComfy()
    req = make_req(tmp_path, kind=GenKind.POSE, pose_mode="precise", width=832, height=1216)
    req.negative_prompt = ""
    res = await make_engine(fake).generate(req)
    assert res.workflow_name == "qwen_edit_pose_cutout"
    sent = fake.prompts[0]["prompt"]
    assert len(fake.uploads) == 1  # 角色參考圖
    ref = by_title(sent, "CHARACTER_REF")["image"]
    assert ref.endswith("_char.png") and ref == res.params["character_image"]
    assert by_title(sent, "POSITIVE")["prompt"] == "a cat"
    assert by_title(sent, "SAMPLER")["seed"] == 7
    assert by_title(sent, "LATENT")["width"] == 832 and by_title(sent, "LATENT")["height"] == 1216
    assert not any(k.startswith("ipadapter") for k in res.params)
    assert "negative_prompt" not in res.params
    assert res.params["steps"] == 4 and res.params["cfg"] == 1.0
    assert "variation" not in res.params
    assert res.extra_images == {"mask": fake.mask_image}


async def test_precise_pose_records_variation_in_params(tmp_path):
    fake = FakeComfy()
    req = make_req(tmp_path, kind=GenKind.POSE, pose_mode="precise", variation="facing left",
                   width=832, height=1216)
    res = await make_engine(fake).generate(req)
    assert res.params["variation"] == "facing left"


async def test_unknown_pose_mode_errors(tmp_path):
    with pytest.raises(EngineError, match="未知的角色動作模式"):
        await make_engine(FakeComfy()).generate(make_req(tmp_path, kind=GenKind.POSE, pose_mode="qwen"))
