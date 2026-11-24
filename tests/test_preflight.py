import copy

import httpx
import pytest

from app.services.comfy_client import ComfyClient
from app.services.preflight import combo_options, format_report, run_preflight
from app.services.workflow_registry import WorkflowRegistry
from tests.conftest import REPO, TINY_WORKFLOW, set_options, write_workflow
from tests.fake_comfy import FakeComfy

ROUTE_A_MODELS = {
    ("CheckpointLoaderSimple", "ckpt_name"): ["sd_xl_base_1.0.safetensors"],
    ("IPAdapterModelLoader", "ipadapter_file"): ["ip-adapter-plus_sdxl_vit-h.safetensors"],
    ("CLIPVisionLoader", "clip_name"): ["CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"],
    ("ControlNetLoader", "control_net_name"): ["controlnet-union-sdxl-1.0-promax.safetensors"],
}


def tiny_object_info(subset: dict) -> dict:
    oi = {k: copy.deepcopy(subset[k]) for k in
          ("CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode",
           "LoadImage", "SaveImage")}
    set_options(oi, "CheckpointLoaderSimple", "ckpt_name", ["base.safetensors"])
    return oi


def test_combo_options_formats():
    assert combo_options([["a", "b"], {}]) == ["a", "b"]
    assert combo_options(["COMBO", {"options": ["x"]}]) == ["x"]
    assert combo_options(["INT", {"min": 0}]) is None
    assert combo_options("MODEL") is None


async def test_all_ok(workflows_dir, object_info_subset):
    fake = FakeComfy(object_info=tiny_object_info(object_info_subset))
    async with fake.client() as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load())
    assert rep.ok, format_report(rep)
    assert rep.comfy_version == "0.36.0"


async def test_reports_missing_nodes_models_and_bad_inputs(workflows_dir, object_info_subset):
    oi = tiny_object_info(object_info_subset)
    set_options(oi, "CheckpointLoaderSimple", "ckpt_name", ["other.safetensors"])
    del oi["VAEDecode"]
    wf = copy.deepcopy(TINY_WORKFLOW)
    wf["5"]["inputs"]["scheduler"] = "bogus"
    del wf["5"]["inputs"]["denoise"]
    wf["5"]["inputs"]["extra_field"] = 1
    write_workflow(workflows_dir, "tiny", wf)
    fake = FakeComfy(object_info=oi)
    async with fake.client() as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load())
    w = rep.workflows[0]
    assert not rep.ok and not w.ok
    assert [n.class_type for n in w.missing_nodes] == ["VAEDecode"]
    assert [m.filename for m in w.missing_models] == ["base.safetensors"]
    assert not any("ckpt_name" in e for e in w.input_errors)  # 不重複回報
    assert any("scheduler='bogus'" in e for e in w.input_errors)
    assert any("缺少必填輸入 denoise" in e for e in w.input_errors)
    assert any("extra_field" in e for e in w.input_warnings)
    text = format_report(rep)
    assert "缺節點：VAEDecode" in text and "缺模型：checkpoints/base.safetensors" in text


async def test_enum_choices_checked_against_node(workflows_dir, object_info_subset):
    import yaml
    m = yaml.safe_load((workflows_dir / "tiny" / "manifest.yaml").read_text())
    m["parameters"]["sampler_name"]["choices"] = ["euler", "not_a_sampler"]
    (workflows_dir / "tiny" / "manifest.yaml").write_text(yaml.safe_dump(m))
    fake = FakeComfy(object_info=tiny_object_info(object_info_subset))
    async with fake.client() as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load())
    assert any("not_a_sampler" in e for e in rep.workflows[0].input_errors)


async def test_model_folder_fallback(tmp_path, object_info_subset):
    import yaml
    d = write_workflow(tmp_path)
    m = yaml.safe_load((d / "manifest.yaml").read_text())
    m["models"].append({"id": "ip", "filename": "ip.safetensors", "folder": "ipadapter"})
    m["models"].append({"id": "q", "filename": "q.gguf", "folder": "unet", "comfy_folder": "unet_gguf"})
    (d / "manifest.yaml").write_text(yaml.safe_dump(m))
    fake = FakeComfy(object_info=tiny_object_info(object_info_subset), models={"unet_gguf": ["q.gguf"]})
    async with fake.client() as c:
        rep = await run_preflight(c, WorkflowRegistry(tmp_path).load())
    missing = rep.workflows[0].missing_models
    assert [x.id for x in missing] == ["ip"]
    assert "沒有註冊資料夾類型" in missing[0].reason


async def test_unreachable_comfy_does_not_raise(workflows_dir):
    def boom(request):
        raise httpx.ConnectError("refused")

    async with ComfyClient("http://x", transport=httpx.MockTransport(boom)) as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load())
    assert not rep.ok and not rep.comfy_reachable
    assert "無法連線" in rep.comfy_error
    assert "未檢查節點與模型" in format_report(rep)


# Route A 已移到 archive/（preflight 不掃描）。下面兩個測試仍以它對照真實 /object_info 驗證 preflight 本身的檢查邏輯
# （下拉值、缺自訂節點時的 provided_by、退回資料夾類型檢查）。
ARCHIVE = REPO / "archive" / "comfy_workflows"


async def test_repo_route_a_against_real_object_info(object_info_subset):
    """Route A 每個節點類別、輸入欄位、下拉值都對照真實 /object_info（ComfyUI 0.36.0 + IPAdapter_plus）。"""
    oi = copy.deepcopy(object_info_subset)
    for (ct, inp), opts in ROUTE_A_MODELS.items():
        set_options(oi, ct, inp, opts)
    reg = WorkflowRegistry(ARCHIVE).load()
    reg.entries = {"route_a_sdxl_ipadapter_cn": reg.get("route_a_sdxl_ipadapter_cn")}
    fake = FakeComfy(object_info=oi)
    async with fake.client() as c:
        rep = await run_preflight(c, reg)
    w = rep.workflows[0]
    assert rep.ok, format_report(rep)
    assert not w.input_warnings


async def test_repo_route_a_without_ipadapter_node(object_info_subset):
    oi = copy.deepcopy(object_info_subset)
    del oi["IPAdapterAdvanced"], oi["IPAdapterModelLoader"]
    reg = WorkflowRegistry(ARCHIVE).load()
    reg.entries = {"route_a_sdxl_ipadapter_cn": reg.get("route_a_sdxl_ipadapter_cn")}
    fake = FakeComfy(object_info=oi)
    async with fake.client() as c:
        rep = await run_preflight(c, reg)
    w = rep.workflows[0]
    assert {n.class_type for n in w.missing_nodes} == {"IPAdapterAdvanced", "IPAdapterModelLoader"}
    assert all(n.provided_by == "https://github.com/cubiq/ComfyUI_IPAdapter_plus" for n in w.missing_nodes)
    # 缺 ipadapter 載入節點時退回檢查資料夾類型；未註冊 → 回報
    assert any(m.id == "ipadapter_plus_sdxl" for m in w.missing_models)


QWEN_WORKFLOWS = {"qwen_character", "qwen_background", "qwen_edit_pose_cutout", "qwen_edit_cleanup"}


async def test_preflight_lists_only_qwen():
    """Phase 9：comfy_workflows/ 只剩四個 Qwen workflow；preflight 只檢查它們的模型（不再有 SDXL、IP-Adapter、ControlNet）。"""
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    assert set(reg.entries) == QWEN_WORKFLOWS
    fake = FakeComfy(object_info={})
    async with fake.client() as c:
        rep = await run_preflight(c, reg)
    assert {w.name for w in rep.workflows} == QWEN_WORKFLOWS
    missing = {m.filename for w in rep.workflows for m in w.missing_models}
    assert missing == {
        "qwen-image-edit-2511-Q4_K_M.gguf",
        "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "qwen_image_vae.safetensors",
        "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
        "birefnet.safetensors",
    }


async def test_caption_font_is_a_separate_item(workflows_dir, object_info_subset, tmp_path):
    fake = FakeComfy(object_info=tiny_object_info(object_info_subset))
    font = REPO / "assets" / "fonts" / "NotoSansTC-VF.ttf"
    async with fake.client() as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load(), caption_font=font)
    assert rep.ok and rep.caption_font.ok and rep.caption_font.path == str(font)
    assert rep.caption_font.error is None

    missing = tmp_path / "missing.ttf"
    async with fake.client() as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load(), caption_font=missing)
    # 字型缺漏另外列出，不影響其他檢查（workflow 照樣通過、整體 ok 不變）
    assert rep.ok and all(w.ok for w in rep.workflows)
    assert rep.caption_font.ok is False and "missing.ttf" in rep.caption_font.error
    assert rep.to_dict()["caption_font"]["ok"] is False
    text = format_report(rep)
    assert "頁面文字字型" in text and "missing.ttf" in text


async def test_caption_font_reported_when_comfy_is_down(workflows_dir, tmp_path):
    def boom(request):
        raise httpx.ConnectError("refused")

    async with ComfyClient("http://comfy.test", transport=httpx.MockTransport(boom)) as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load(), caption_font=tmp_path / "x.ttf")
    assert not rep.comfy_reachable and rep.caption_font.ok is False


async def test_preflight_workflow_exception_reported(workflows_dir, object_info_subset):
    """A8：格式怪的 /object_info 或 /models 回應只讓該 workflow 的檢查失敗，不冒成例外。"""
    oi = tiny_object_info(object_info_subset)
    oi["KSampler"] = "garbage"  # _check_inputs 會在這裡丟例外
    fake = FakeComfy(object_info=oi)
    async with fake.client() as c:
        rep = await run_preflight(c, WorkflowRegistry(workflows_dir).load())
    w = rep.workflows[0]
    assert not rep.ok and not w.ok
    assert any("檢查失敗" in e for e in w.load_errors)


async def test_list_models_non_json_is_connection_error():
    from app.services.comfy_client import ComfyConnectionError

    def html(request):
        return httpx.Response(200, text="<html>oops</html>")

    async with ComfyClient("http://x", transport=httpx.MockTransport(html)) as c:
        with pytest.raises(ComfyConnectionError):
            await c.list_models("checkpoints")


async def test_preflight_cleanup_workflow_needs_no_birefnet():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    async with FakeComfy(object_info={}).client() as c:
        rep = await run_preflight(c, reg)
    w = next(w for w in rep.workflows if w.name == "qwen_edit_cleanup")
    assert {m.filename for m in w.missing_models} == {
        "qwen-image-edit-2511-Q4_K_M.gguf", "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "qwen_image_vae.safetensors", "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"}
    assert {n.class_type for n in w.missing_nodes} >= {"UnetLoaderGGUF", "TextEncodeQwenImageEditPlus"}
    assert "RemoveBackground" not in {n.class_type for n in w.missing_nodes}
