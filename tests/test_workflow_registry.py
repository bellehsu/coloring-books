import copy
import json

import pytest

from app.services.workflow_registry import (
    ParameterError,
    WorkflowNotReady,
    WorkflowRegistry,
    load_workflow_dir,
)
from tests.conftest import REPO, TINY_MANIFEST, TINY_WORKFLOW, write_workflow


def test_load_ok_and_hashes(workflows_dir):
    reg = WorkflowRegistry(workflows_dir).load()
    e = reg.get("tiny")
    assert e.ok, e.errors
    assert len(e.workflow_hash) == 64 and len(e.version) == 12
    assert e.class_types() >= {"KSampler", "SaveImage"}


def test_version_changes_when_files_change(workflows_dir):
    v1 = load_workflow_dir(workflows_dir / "tiny").version
    wf = copy.deepcopy(TINY_WORKFLOW)
    wf["5"]["inputs"]["denoise"] = 0.9
    (workflows_dir / "tiny" / "workflow_api.json").write_text(json.dumps(wf))
    assert load_workflow_dir(workflows_dir / "tiny").version != v1


def test_build_prompt_applies_whitelisted_params(workflows_dir):
    e = WorkflowRegistry(workflows_dir).load().get("tiny")
    built = e.build_prompt({"prompt": "a cat", "seed": 7, "size": 640, "ref_image": "coloring_books/r.png"})
    p = built.prompt
    assert p["2"]["inputs"]["text"] == "a cat"
    assert p["5"]["inputs"]["seed"] == 7
    assert p["5"]["inputs"]["steps"] == 10  # default
    assert p["4"]["inputs"]["width"] == p["4"]["inputs"]["height"] == 640  # 多 target
    assert p["2"]["inputs"]["clip"] == ["1", 1]  # 連線不受影響
    # 沒提供也沒有 default 的選填參數：沿用 workflow 原值並記錄
    assert built.params["negative_prompt"] == ""
    assert built.params["cfg"] == 5.0 and built.params["seed"] == 7
    assert built.models == ["base.safetensors"]
    assert built.output_node_ids == ["8"]
    # 原始 workflow 不被修改
    assert e.workflow["2"]["inputs"]["text"] == "hello"


def test_build_prompt_rejects_unknown_and_invalid(workflows_dir):
    e = WorkflowRegistry(workflows_dir).load().get("tiny")
    with pytest.raises(ParameterError) as ei:
        e.build_prompt({"prompt": "x" * 101, "seed": -1, "steps": True, "cfg": "high",
                        "sampler_name": "bogus", "ref_image": "../etc/passwd", "denoise": 0.5})
    probs = " ".join(ei.value.problems)
    for word in ("denoise", "prompt", "seed", "steps", "cfg", "sampler_name", "ref_image"):
        assert word in probs
    with pytest.raises(ParameterError, match="必填"):
        e.build_prompt({"prompt": "x", "ref_image": "a.png"})


def test_int_accepts_integral_float(workflows_dir):
    e = WorkflowRegistry(workflows_dir).load().get("tiny")
    built = e.build_prompt({"prompt": "x", "seed": 3.0, "ref_image": "a.png"})
    assert built.prompt["5"]["inputs"]["seed"] == 3 and isinstance(built.prompt["5"]["inputs"]["seed"], int)


@pytest.mark.parametrize("mutate, expected", [
    (lambda m, w: m["parameters"]["prompt"]["targets"].__setitem__(0, {"title": "NOPE", "input": "text"}),
     "找不到 title"),
    (lambda m, w: w["3"]["_meta"].__setitem__("title", "POSITIVE"), "重複"),
    (lambda m, w: m["parameters"]["prompt"]["targets"].__setitem__(0, {"title": "POSITIVE", "input": "clip"}),
     "是連線"),
    (lambda m, w: m["parameters"]["prompt"]["targets"].__setitem__(0, {"title": "POSITIVE", "input": "zzz"}),
     "沒有輸入欄位"),
    (lambda m, w: m["parameters"]["steps"].__setitem__("default", 99), "預設值不合法"),
    (lambda m, w: m["models"][0].__setitem__("filename", "other.safetensors"), "不一致"),
    (lambda m, w: m["parameters"]["negative_prompt"].__setitem__(
        "targets", [{"title": "POSITIVE", "input": "text"}]), "同一個輸入"),
    (lambda m, w: m["outputs"].__setitem__(0, {"title": "MISSING"}), "outputs"),
])
def test_semantic_validation(tmp_path, mutate, expected):
    m, w = copy.deepcopy(TINY_MANIFEST), copy.deepcopy(TINY_WORKFLOW)
    mutate(m, w)
    e = load_workflow_dir(write_workflow(tmp_path, "tiny", w, m))
    assert not e.ok
    assert any(expected in err for err in e.errors), e.errors


@pytest.mark.parametrize("mutate, expected", [
    (lambda m: m.pop("outputs"), "outputs"),
    (lambda m: m["parameters"]["seed"].pop("max"), "max"),
    (lambda m: m["parameters"]["sampler_name"].pop("choices"), "choices"),
    (lambda m: m["parameters"]["seed"].__setitem__("type", "number"), "number"),
    (lambda m: m["parameters"]["seed"].__setitem__("evil", 1), "evil"),
])
def test_schema_validation(tmp_path, mutate, expected):
    m = copy.deepcopy(TINY_MANIFEST)
    mutate(m)
    e = load_workflow_dir(write_workflow(tmp_path, "tiny", None, m))
    assert not e.ok
    assert any("schema" in err and expected in err for err in e.errors), e.errors


def test_missing_workflow_file_and_ui_format(tmp_path):
    e = load_workflow_dir(write_workflow(tmp_path, "a", False))
    assert any("缺少 workflow 檔" in x for x in e.errors)
    with pytest.raises(WorkflowNotReady):
        e.build_prompt({})
    d = write_workflow(tmp_path, "b")
    (d / "workflow_api.json").write_text(json.dumps({"nodes": [], "links": []}))
    assert any("UI 格式" in x for x in load_workflow_dir(d).errors)


def test_name_must_match_folder(tmp_path):
    d = write_workflow(tmp_path, "tiny")
    (d.parent / "renamed").mkdir()
    for f in d.iterdir():
        (d.parent / "renamed" / f.name).write_bytes(f.read_bytes())
    e = load_workflow_dir(d.parent / "renamed")
    assert any("資料夾名稱" in x for x in e.errors)


def test_one_bad_workflow_does_not_block_others(tmp_path):
    write_workflow(tmp_path, "good")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.yaml").write_text("{ not: [valid")
    reg = WorkflowRegistry(tmp_path).load()
    assert reg.get("good").ok
    assert not reg.get("bad").ok
    with pytest.raises(KeyError, match="可用"):
        reg.get("nope")


# ---- 專案內真正的 workflow --------------------------------------------------------

def test_repo_registry_has_no_retired_routes():
    """Phase 9：SDXL（含 IP-Adapter、ControlNet）與 Route B 的 workflow 都在 archive/，不會被登錄。"""
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    retired = {"route_b_qwen_edit_2511_gguf", "sdxl_text2img", "sdxl_background", "sdxl_pose_cutout",
               "route_a_sdxl_ipadapter_cn"}
    assert not retired & set(reg.entries)
    assert retired <= {p.name for p in (REPO / "archive" / "comfy_workflows").iterdir()}


def _assert_no_orphans(prompt):
    refs = {v[0] for n in prompt.values() for v in n["inputs"].values()
            if isinstance(v, list) and len(v) == 2}
    orphans = [nid for nid, n in prompt.items()
               if nid not in refs and n["class_type"] != "SaveImage"]
    assert not orphans, orphans


def test_qwen_pose_manifest_loads_and_maps_titles():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    wf = reg.get("qwen_edit_pose_cutout")
    assert wf.ok, wf.errors
    built = wf.build_prompt({"prompt": "p", "seed": 1, "width": 832, "height": 1216,
                             "character_image": "c.png"})
    by_title = {n["_meta"]["title"]: n for n in built.prompt.values()}
    assert by_title["CHARACTER_REF"]["inputs"]["image"] == "c.png"
    assert by_title["POSITIVE"]["inputs"]["prompt"] == "p"
    assert by_title["NEGATIVE"]["inputs"]["prompt"] == ""
    s = by_title["SAMPLER"]["inputs"]
    assert (s["seed"], s["steps"], s["cfg"]) == (1, 4, 1)
    assert (by_title["LATENT"]["inputs"]["width"], by_title["LATENT"]["inputs"]["height"]) == (832, 1216)
    assert {f"{m['folder']}/{m['filename']}" for m in wf.models} == {
        "unet/qwen-image-edit-2511-Q4_K_M.gguf",
        "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "vae/qwen_image_vae.safetensors",
        "loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
        "background_removal/birefnet.safetensors"}
    assert [by_title[t]["class_type"] for t in ("OUTPUT", "MASK")] == ["SaveImage"] * 2
    assert len(built.output_node_ids) == 2


def _trace(prompt, nid, inp, target_title):
    """沿著 inputs 往上游找 target_title；找到回傳 True。"""
    seen, stack = set(), [prompt[nid]["inputs"][inp][0]]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if prompt[cur]["_meta"]["title"] == target_title:
            return True
        stack += [v[0] for v in prompt[cur]["inputs"].values()
                  if isinstance(v, list) and len(v) == 2]
    return False


def test_qwen_pose_wiring():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    p = reg.get("qwen_edit_pose_cutout").build_prompt(
        {"prompt": "x", "seed": 1, "character_image": "c.png"}).prompt
    t = {n["_meta"]["title"]: nid for nid, n in p.items()}
    assert _trace(p, t["SAMPLER"], "model", "LIGHTNING_LORA")
    assert _trace(p, t["LIGHTNING_LORA"], "model", "UNET")
    assert _trace(p, t["SAMPLER"], "model", "CFG_NORM")
    assert _trace(p, t["SAMPLER"], "model", "MODEL_SAMPLING")
    for neg in ("POSITIVE", "NEGATIVE"):
        assert p[t[neg]]["inputs"]["image1"][0] == t["CHARACTER_REF"]
    assert p[t["SAMPLER"]]["inputs"]["negative"] and _trace(p, t["SAMPLER"], "negative", "NEGATIVE")
    assert p[t["REMOVE_BG"]]["inputs"]["image"][0] == t["DECODE"]
    assert p[t["OUTPUT"]]["inputs"]["images"][0] == t["DECODE"]
    _assert_no_orphans(p)


def test_qwen_background_manifest_loads():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    wf = reg.get("qwen_background")
    assert wf.ok, wf.errors
    assert set(wf.params) == {"prompt", "seed", "width", "height"}
    assert {f"{m['folder']}/{m['filename']}" for m in wf.models} == {
        "unet/qwen-image-edit-2511-Q4_K_M.gguf",
        "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "vae/qwen_image_vae.safetensors",
        "loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"}
    built = wf.build_prompt({"prompt": "p", "seed": 3})
    p = built.prompt
    classes = {n["class_type"] for n in p.values()}
    assert "LoadImage" not in classes
    assert not classes & {"LoadBackgroundRemovalModel", "RemoveBackground"}
    assert not any("birefnet" in json.dumps(n).lower() for n in p.values())
    by_title = {n["_meta"]["title"]: n for n in p.values()}
    assert set(by_title) == {"UNET", "TEXT_ENCODER", "VAE", "MODEL_SAMPLING", "CFG_NORM", "LIGHTNING_LORA",
                             "POSITIVE", "NEGATIVE", "LATENT", "SAMPLER", "DECODE", "OUTPUT"}
    assert by_title["POSITIVE"]["inputs"]["prompt"] == "p"
    assert by_title["NEGATIVE"]["inputs"]["prompt"] == ""
    for t in ("POSITIVE", "NEGATIVE"):
        assert by_title[t]["class_type"] == "TextEncodeQwenImageEditPlus"
        assert "image1" not in by_title[t]["inputs"]
    s = by_title["SAMPLER"]["inputs"]
    assert (s["seed"], s["steps"], s["cfg"], s["sampler_name"], s["scheduler"], s["denoise"]) == (
        3, 4, 1, "euler", "simple", 1.0)
    assert (by_title["LATENT"]["inputs"]["width"], by_title["LATENT"]["inputs"]["height"]) == (864, 1216)
    assert by_title["LATENT"]["class_type"] == "EmptySD3LatentImage"
    assert by_title["MODEL_SAMPLING"]["inputs"]["shift"] == 3.1
    assert by_title["CFG_NORM"]["inputs"]["strength"] == 1.0
    assert by_title["LIGHTNING_LORA"]["inputs"]["strength_model"] == 1.0
    assert by_title["OUTPUT"]["class_type"] == "SaveImage"
    assert len(built.output_node_ids) == 1
    for bad in ({"prompt": "p", "seed": 1, "width": 511}, {"prompt": "p", "seed": 1, "height": 1537},
                {"prompt": "p", "seed": 1, "character_image": "c.png"}):
        with pytest.raises(ParameterError):
            wf.build_prompt(bad)


def test_qwen_background_wiring():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    p = reg.get("qwen_background").build_prompt({"prompt": "x", "seed": 1}).prompt
    t = {n["_meta"]["title"]: nid for nid, n in p.items()}
    assert p[t["SAMPLER"]]["inputs"]["model"][0] == t["LIGHTNING_LORA"]
    assert p[t["LIGHTNING_LORA"]]["inputs"]["model"][0] == t["CFG_NORM"]
    assert p[t["CFG_NORM"]]["inputs"]["model"][0] == t["MODEL_SAMPLING"]
    assert p[t["MODEL_SAMPLING"]]["inputs"]["model"][0] == t["UNET"]
    assert p[t["SAMPLER"]]["inputs"]["positive"][0] == t["POSITIVE"]
    assert p[t["SAMPLER"]]["inputs"]["negative"][0] == t["NEGATIVE"]
    assert p[t["SAMPLER"]]["inputs"]["latent_image"][0] == t["LATENT"]
    for enc in ("POSITIVE", "NEGATIVE"):
        assert p[t[enc]]["inputs"]["clip"][0] == t["TEXT_ENCODER"]
        assert p[t[enc]]["inputs"]["vae"][0] == t["VAE"]
    assert p[t["DECODE"]]["inputs"]["samples"][0] == t["SAMPLER"]
    assert p[t["DECODE"]]["inputs"]["vae"][0] == t["VAE"]
    assert p[t["OUTPUT"]]["inputs"]["images"][0] == t["DECODE"]
    _assert_no_orphans(p)


def test_registry_loads_qwen_character():
    # 角色參考圖：與 qwen_background 同一張 Qwen 純文字生圖，預設尺寸為 QWEN_CHARACTER_SIZE
    from app.services.prompts import QWEN_CHARACTER_SIZE

    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    wf = reg.get("qwen_character")
    assert wf.ok, wf.errors
    assert wf.manifest["display_name"] == "Qwen 角色參考圖（白底全身）"
    assert set(wf.params) == {"prompt", "seed", "width", "height"}
    assert {f"{m['folder']}/{m['filename']}" for m in wf.models} == {
        "unet/qwen-image-edit-2511-Q4_K_M.gguf",
        "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "vae/qwen_image_vae.safetensors",
        "loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"}
    built = wf.build_prompt({"prompt": "一隻小白兔", "seed": 3})
    p = built.prompt
    by_title = {n["_meta"]["title"]: n for n in p.values()}
    assert set(by_title) == {"UNET", "TEXT_ENCODER", "VAE", "MODEL_SAMPLING", "CFG_NORM", "LIGHTNING_LORA",
                             "POSITIVE", "NEGATIVE", "LATENT", "SAMPLER", "DECODE", "OUTPUT"}
    assert by_title["POSITIVE"]["inputs"]["prompt"] == "一隻小白兔"
    assert by_title["NEGATIVE"]["inputs"]["prompt"] == ""
    for t in ("POSITIVE", "NEGATIVE"):
        assert "image1" not in by_title[t]["inputs"]
    assert "LoadImage" not in {n["class_type"] for n in p.values()}
    assert (by_title["LATENT"]["inputs"]["width"], by_title["LATENT"]["inputs"]["height"]) == QWEN_CHARACTER_SIZE
    assert by_title["SAMPLER"]["inputs"]["seed"] == 3
    assert by_title["OUTPUT"]["inputs"]["filename_prefix"] == "coloring_books/character"
    assert len(built.output_node_ids) == 1
    # 同一張圖：除了預設尺寸與輸出檔名前綴，節點與固定值都和 qwen_background 一樣
    bg = reg.get("qwen_background").build_prompt({"prompt": "一隻小白兔", "seed": 3, "width": 832}).prompt
    for nid in p:
        if by_title.get("OUTPUT") is p[nid]:
            continue
        assert p[nid] == bg[nid], nid
    for bad in ({"prompt": "p", "seed": 1, "character_image": "c.png"},
                {"prompt": "p", "seed": 1, "negative_prompt": "x"}):
        with pytest.raises(ParameterError):
            wf.build_prompt(bad)


BIREFNET_URL = ("https://huggingface.co/Comfy-Org/BiRefNet/resolve/main/"
                "background_removal/birefnet.safetensors")


def test_birefnet_source_matches_comfy_blueprint():
    # ComfyUI 內建「Remove Background (BiRefNet)」藍圖下載的檔案
    wf = WorkflowRegistry(REPO / "comfy_workflows").load().get("qwen_edit_pose_cutout")
    m = next(m for m in wf.models if m["filename"] == "birefnet.safetensors")
    assert (m["folder"], m["source"]) == ("background_removal", BIREFNET_URL)


def test_qwen_cleanup_manifest_loads_and_maps_titles():
    wf = WorkflowRegistry(REPO / "comfy_workflows").load().get("qwen_edit_cleanup")
    assert wf.ok, wf.errors
    assert set(wf.manifest["parameters"]) == {"prompt", "source_image", "lora_strength", "width", "height",
                                              "seed", "steps", "cfg"}
    assert [o["title"] for o in wf.manifest["outputs"]] == ["OUTPUT"]
    pose = WorkflowRegistry(REPO / "comfy_workflows").load().get("qwen_edit_pose_cutout")
    assert [m["filename"] for m in wf.manifest["models"]] == \
        [m["filename"] for m in pose.manifest["models"] if m["id"] != "birefnet"]
    built = wf.build_prompt({"prompt": "p", "seed": 1, "width": 1216, "height": 832, "source_image": "s.png"})
    by_title = {n["_meta"]["title"]: n for n in built.prompt.values()}
    assert by_title["SOURCE_IMAGE"]["inputs"]["image"] == "s.png"
    assert by_title["POSITIVE"]["inputs"]["prompt"] == "p" and by_title["NEGATIVE"]["inputs"]["prompt"] == ""
    assert (by_title["LATENT"]["inputs"]["width"], by_title["LATENT"]["inputs"]["height"]) == (1216, 832)
    assert not {"BG_REMOVAL_MODEL", "REMOVE_BG", "MASK_TO_IMAGE", "MASK"} & set(by_title)
    _assert_no_orphans(built.prompt)
