"""ComfyEngine（Phase 9）：只有三條 Qwen 路線；其他種類一律「這種生成已停用」，絕不偷換模型。"""
from pathlib import Path

import pytest

from app.models import GenKind
from app.services import comfy_engine
from app.services.comfy_engine import RETIRED, WORKFLOWS, ComfyEngine
from app.services.engine import EngineError, EngineUnavailable, GenerationRequest
from app.services.workflow_registry import WorkflowRegistry
from tests.conftest import REPO
from tests.fake_comfy import FakeComfy, png_bytes

QWEN_MODELS = {
    "qwen-image-edit-2511-Q4_K_M.gguf",
    "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "qwen_image_vae.safetensors",
    "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
}


def make_req(tmp_path: Path, kind=GenKind.CHARACTER, character=False, width=832, height=1216, **kw):
    char = png_bytes() if character else None  # 引擎收 bytes（worker 從 Storage 讀出）
    return GenerationRequest(kind=kind, prompt="a cat", negative_prompt="", seed=7, width=width, height=height,
                             character_image=char, **kw)


def make_engine(fake: FakeComfy, registry: WorkflowRegistry | None = None) -> ComfyEngine:
    registry = registry or WorkflowRegistry(REPO / "comfy_workflows").load()
    return ComfyEngine(lambda: fake.client(), registry, run_timeout_s=10)


def by_title(prompt: dict, title: str) -> dict:
    return next(n for n in prompt.values() if n["_meta"]["title"] == title)["inputs"]


def test_workflows_are_three_qwen_routes():
    assert WORKFLOWS == {GenKind.CHARACTER: "qwen_character", GenKind.BACKGROUND: "qwen_background",
                         GenKind.POSE: "qwen_edit_pose_cutout"}
    assert RETIRED == "這種生成已停用"
    for gone in ("PAGE_FIXED", "page_params", "_white_png", "BACKGROUND_WORKFLOWS", "POSE_WORKFLOWS",
                 "CHARACTER_WORKFLOW", "PAGE_WORKFLOW"):
        assert not hasattr(comfy_engine, gone), gone


def test_generation_request_has_no_sdxl_fields():
    req = GenerationRequest(kind=GenKind.CHARACTER, prompt="p", negative_prompt="", seed=1, width=8, height=8)
    for gone in ("structure_image", "structure_strength", "pose_mode", "background_style"):
        assert not hasattr(req, gone), gone
    assert req.variation is None and req.character_image is None


@pytest.mark.parametrize(("kind", "workflow", "character"), [
    (GenKind.CHARACTER, "qwen_character", False),
    (GenKind.BACKGROUND, "qwen_background", False),
    (GenKind.POSE, "qwen_edit_pose_cutout", True),
])
async def test_engine_routes_three_kinds(tmp_path, kind, workflow, character):
    fake = FakeComfy()
    res = await make_engine(fake).generate(make_req(tmp_path, kind=kind, character=character, width=864))
    assert res.workflow_name == workflow
    assert res.engine == "comfyui" and res.image == fake.image
    assert set(res.models) == QWEN_MODELS | ({"birefnet.safetensors"} if kind == GenKind.POSE else set())
    sent = fake.prompts[0]["prompt"]
    assert by_title(sent, "POSITIVE")["prompt"] == "a cat"
    assert by_title(sent, "NEGATIVE")["prompt"] == ""
    assert by_title(sent, "SAMPLER")["seed"] == 7
    assert by_title(sent, "LATENT")["width"] == 864 and by_title(sent, "LATENT")["height"] == 1216
    # 沒有 SDXL 的東西：negative_prompt、結構圖、ControlNet、IP-Adapter
    assert "negative_prompt" not in res.params
    assert not any(k.startswith(("ipadapter", "controlnet", "structure")) for k in res.params)
    assert not any(n["class_type"] in ("ControlNetApplyAdvanced", "CheckpointLoaderSimple")
                   or "IPAdapter" in n["class_type"] for n in sent.values())
    assert (res.params["prompt"], res.params["seed"], res.params["width"], res.params["height"]) == (
        "a cat", 7, 864, 1216)
    if kind == GenKind.POSE:
        # 角色參考圖上傳到 ComfyUI，名稱寫進 CHARACTER_REF 與結果參數
        assert len(fake.uploads) == 1 and fake.uploads[0]["subfolder"] == "coloring_books"
        ref = by_title(sent, "CHARACTER_REF")["image"]
        assert ref.endswith("_char.png") and ref == res.params["character_image"]
        # 上傳的是請求裡的 bytes（檔名是內容 hash）
        import hashlib
        assert hashlib.sha256(png_bytes()).hexdigest()[:12] in ref
        assert res.extra_images == {"mask": fake.mask_image}
    else:
        assert fake.uploads == []
        assert "character_image" not in res.params
        assert res.extra_images == {}
        assert not any(n["class_type"] == "LoadImage" for n in sent.values())


async def test_character_ignores_stray_character_image(tmp_path):
    # 角色參考圖是純文字生圖：就算請求帶了圖也不上傳、不送出
    fake = FakeComfy()
    res = await make_engine(fake).generate(make_req(tmp_path, kind=GenKind.CHARACTER, character=True))
    assert res.workflow_name == "qwen_character"
    assert fake.uploads == [] and "character_image" not in res.params


async def test_character_progress_and_defaults(tmp_path):
    seen = []
    fake = FakeComfy()
    res = await make_engine(fake).generate(make_req(tmp_path), on_progress=lambda v, m: seen.append((v, m)))
    assert (1, 2) in seen and (2, 2) in seen
    sent = fake.prompts[0]["prompt"]
    s = by_title(sent, "SAMPLER")
    assert (s["steps"], s["cfg"]) == (4, 1.0)
    assert by_title(sent, "OUTPUT")["filename_prefix"] == "coloring_books/character"
    assert res.params == {"prompt": "a cat", "seed": 7, "width": 832, "height": 1216}


async def test_pose_upload_is_cached(tmp_path):
    fake = FakeComfy()
    eng = make_engine(fake)
    req = make_req(tmp_path, kind=GenKind.POSE, character=True)
    await eng.generate(req)
    await eng.generate(req)
    assert len(fake.uploads) == 1


async def test_engine_rejects_page_kind(tmp_path):
    fake = FakeComfy()
    with pytest.raises(EngineError, match="^這種生成已停用$") as ei:
        await make_engine(fake).generate(make_req(tmp_path, kind=GenKind.PAGE, character=True))
    assert not isinstance(ei.value, EngineUnavailable)
    assert fake.prompts == [] and fake.uploads == []


async def test_engine_rejects_composite_kind(tmp_path):
    # 合成在後端 Pillow 做，不會進引擎；萬一進來也不能送到任何 workflow
    fake = FakeComfy()
    with pytest.raises(EngineError, match="^這種生成已停用$"):
        await make_engine(fake).generate(make_req(tmp_path, kind=GenKind.COMPOSITE))
    assert fake.prompts == []


@pytest.mark.parametrize(("kind", "character"), [
    (GenKind.CHARACTER, False), (GenKind.BACKGROUND, False), (GenKind.POSE, True)])
async def test_variation_recorded_for_all_three_kinds(tmp_path, kind, character):
    # 變化語已併入 prompt，另外記在結果參數方便查詢；沒有就不記
    fake = FakeComfy()
    eng = make_engine(fake)
    res = await eng.generate(make_req(tmp_path, kind=kind, character=character, variation="Chubby round shape"))
    assert res.params["variation"] == "Chubby round shape"
    res = await eng.generate(make_req(tmp_path, kind=kind, character=character))
    assert "variation" not in res.params


async def test_engine_errors_mapped(tmp_path):
    fake = FakeComfy()
    eng = make_engine(fake)
    fake.dead = True
    with pytest.raises(EngineUnavailable):
        await eng.generate(make_req(tmp_path))
    fake.dead = False
    fake.mode = "error"
    with pytest.raises(EngineError) as ei:
        await eng.generate(make_req(tmp_path))
    assert not isinstance(ei.value, EngineUnavailable)
    assert "CUDA out of memory" in str(ei.value)


async def test_pose_without_character_image_errors(tmp_path):
    with pytest.raises(EngineError, match="角色動作需要角色參考圖"):
        await make_engine(FakeComfy()).generate(make_req(tmp_path, kind=GenKind.POSE, character=False))


async def test_pose_missing_mask_output_errors(tmp_path):
    fake = FakeComfy()
    fake.skip_output_titles = {"MASK"}
    with pytest.raises(EngineError, match="去背失敗：沒有遮罩輸出"):
        await make_engine(fake).generate(make_req(tmp_path, kind=GenKind.POSE, character=True))


async def test_missing_workflow_is_engine_error(tmp_path):
    # 例如 qwen_character 資料夾不見了：清楚的錯誤，不改用別的模型
    fake = FakeComfy()
    registry = WorkflowRegistry(REPO / "comfy_workflows").load()
    registry.entries.pop("qwen_character")
    with pytest.raises(EngineError, match="qwen_character"):
        await make_engine(fake, registry).generate(make_req(tmp_path))
    assert fake.prompts == []
