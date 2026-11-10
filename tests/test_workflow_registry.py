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

def test_repo_route_a_loads():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    a = reg.get("route_a_sdxl_ipadapter_cn")
    assert a.ok, a.errors
    built = a.build_prompt({"prompt": "x", "seed": 1, "character_image": "c.png", "structure_image": "s.png"})
    assert built.params["controlnet_type"] == "canny/lineart/anime_lineart/mlsd"


def test_repo_registry_has_no_retired_routes():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    assert "route_b_qwen_edit_2511_gguf" not in reg.entries
    assert "sdxl_text2img" in reg.entries
    assert "route_a_sdxl_ipadapter_cn" in reg.entries


def test_repo_compose_workflows_load():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    bg = reg.get("sdxl_background")
    assert bg.ok, bg.errors
    built = bg.build_prompt({"prompt": "x", "seed": 1, "structure_image": "s.png"})
    assert built.params["controlnet_strength"] == 0.0
    assert built.params["controlnet_type"] == "hed/pidi/scribble/ted"
    assert built.params["controlnet_end_percent"] == 0.5
    assert len(built.output_node_ids) == 1
    assert not any(n["class_type"].startswith("IPAdapter") for n in built.prompt.values())

    pose = reg.get("sdxl_pose_cutout")
    assert pose.ok, pose.errors
    built = pose.build_prompt({"prompt": "x", "seed": 1, "character_image": "c.png"})
    assert built.params["ipadapter_weight"] == 0.55
    assert built.params["ipadapter_end_at"] == 0.6
    assert len(built.output_node_ids) == 2
    assert "background_removal/birefnet.safetensors" in {
        f"{m['folder']}/{m['filename']}" for m in pose.models}
    with pytest.raises(ParameterError):
        pose.build_prompt({"prompt": "x", "seed": 1, "character_image": "c.png", "ipadapter_weight": 5.0})


def _src(prompt, title, inp):
    by_title = {n["_meta"]["title"]: nid for nid, n in prompt.items()}
    return prompt[by_title[title]]["inputs"][inp][0], by_title


def _assert_no_orphans(prompt):
    refs = {v[0] for n in prompt.values() for v in n["inputs"].values()
            if isinstance(v, list) and len(v) == 2}
    orphans = [nid for nid, n in prompt.items()
               if nid not in refs and n["class_type"] != "SaveImage"]
    assert not orphans, orphans


def test_repo_compose_workflows_wiring():
    reg = WorkflowRegistry(REPO / "comfy_workflows").load()
    bg = reg.get("sdxl_background").build_prompt(
        {"prompt": "x", "seed": 1, "structure_image": "s.png"}).prompt
    src, t = _src(bg, "SAMPLER", "model")
    assert src == t["CKPT"]
    assert _src(bg, "SAMPLER", "positive")[0] == t["APPLY_CONTROLNET"]
    assert _src(bg, "SAMPLER", "negative")[0] == t["APPLY_CONTROLNET"]
    _assert_no_orphans(bg)

    pose = reg.get("sdxl_pose_cutout").build_prompt(
        {"prompt": "x", "seed": 1, "character_image": "c.png"}).prompt
    src, t = _src(pose, "SAMPLER", "model")
    assert src == t["IPADAPTER"]
    assert _src(pose, "IPADAPTER", "image")[0] == t["CHARACTER_REF"]
    _assert_no_orphans(pose)


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
