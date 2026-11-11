from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def object_info_subset() -> dict:
    """ComfyUI 0.36.0 + IPAdapter_plus + ComfyUI-GGUF 真實 /object_info 的節錄（模型清單已清空）。"""
    return json.loads((FIXTURES / "object_info_subset.json").read_text(encoding="utf-8"))


def set_options(object_info: dict, class_type: str, input_name: str, options: list[str]) -> None:
    for sec in ("required", "optional"):
        spec = (object_info[class_type]["input"].get(sec) or {}).get(input_name)
        if spec is None:
            continue
        if isinstance(spec[0], list):
            spec[0] = options
        else:
            spec[1]["options"] = options
        return
    raise KeyError(f"{class_type}.{input_name}")


TINY_WORKFLOW = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"},
          "_meta": {"title": "CKPT"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["1", 1]},
          "_meta": {"title": "POSITIVE"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}, "_meta": {"title": "NEGATIVE"}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1},
          "_meta": {"title": "LATENT"}},
    "5": {"class_type": "KSampler", "inputs": {
        "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
        "seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0},
        "_meta": {"title": "SAMPLER"}},
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}, "_meta": {"title": "DECODE"}},
    "7": {"class_type": "LoadImage", "inputs": {"image": "x.png"}, "_meta": {"title": "REF"}},
    "8": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "t"},
          "_meta": {"title": "OUTPUT"}},
}

TINY_MANIFEST = {
    "schema_version": 1,
    "name": "tiny",
    "route": "T",
    "parameters": {
        "prompt": {"type": "string", "required": True, "max_length": 100,
                   "targets": [{"title": "POSITIVE", "input": "text"}]},
        "negative_prompt": {"type": "string", "targets": [{"title": "NEGATIVE", "input": "text"}]},
        "seed": {"type": "int", "min": 0, "max": 1000, "required": True,
                 "targets": [{"title": "SAMPLER", "input": "seed"}]},
        "steps": {"type": "int", "min": 1, "max": 50, "default": 10,
                  "targets": [{"title": "SAMPLER", "input": "steps"}]},
        "cfg": {"type": "float", "min": 1, "max": 10, "default": 5.0,
                "targets": [{"title": "SAMPLER", "input": "cfg"}]},
        "sampler_name": {"type": "enum", "choices": ["euler", "dpmpp_2m"], "default": "euler",
                         "targets": [{"title": "SAMPLER", "input": "sampler_name"}]},
        "size": {"type": "int", "min": 256, "max": 1024, "default": 512,
                 "targets": [{"title": "LATENT", "input": "width"}, {"title": "LATENT", "input": "height"}]},
        "ref_image": {"type": "image", "required": True, "targets": [{"title": "REF", "input": "image"}]},
    },
    "outputs": [{"title": "OUTPUT"}],
    "models": [{"id": "base", "filename": "base.safetensors", "folder": "checkpoints",
                "source": "https://example/base", "loader": {"title": "CKPT", "input": "ckpt_name"}}],
}


def write_workflow(root: Path, name: str = "tiny", workflow: dict | None = None,
                   manifest: dict | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    m = json.loads(json.dumps(manifest or TINY_MANIFEST))
    m["name"] = name
    (d / "manifest.yaml").write_text(yaml.safe_dump(m, allow_unicode=True), encoding="utf-8")
    if workflow is not False:
        (d / "workflow_api.json").write_text(json.dumps(workflow or TINY_WORKFLOW), encoding="utf-8")
    return d


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    root = tmp_path / "workflows"
    write_workflow(root)
    return root


@pytest.fixture
def dev_owner() -> str | None:
    """settings_file 的 auth.dev_owner：預設 admin，舊的 API 測試不用邀請連結也能跑。
    要測真正的邀請連結／隔離時，在該測試模組覆寫這個 fixture 回傳 None。"""
    return "admin"


@pytest.fixture
def settings_file(tmp_path: Path, workflows_dir: Path, dev_owner: str | None) -> Path:
    models = tmp_path / "models"
    models.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.safe_dump({
        "comfy": {"url": "http://comfy.test:8188", "models_root": str(models)},
        "paths": {"workflows_dir": str(workflows_dir), "storage_dir": str(storage),
                  "database": str(storage / "app.db")},
        "auth": {"dev_owner": dev_owner},
    }), encoding="utf-8")
    return p
