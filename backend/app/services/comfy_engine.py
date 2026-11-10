"""以 ComfyUI 實作的生成引擎：把 GenerationRequest 對應到 workflow manifest 的白名單參數。"""

from __future__ import annotations

import hashlib
import io
import time
from collections.abc import Callable

from PIL import Image

from app.models import GenKind
from app.services.comfy_client import ComfyClient, ComfyConnectionError, ComfyError, ProgressEvent
from app.services.engine import (
    EngineError, EngineUnavailable, GenerationRequest, GenerationResult, ProgressFn,
)
from app.services.workflow_registry import ParameterError, WorkflowNotReady, WorkflowRegistry

CHARACTER_WORKFLOW = "sdxl_text2img"
PAGE_WORKFLOW = "route_a_sdxl_ipadapter_cn"
# 背景：風格 → workflow（GenerationRequest.background_style；排入佇列時已決定）
BACKGROUND_WORKFLOWS: dict[str, str] = {"simple": "qwen_background", "detailed": "sdxl_background"}
# 角色動作：模式 → workflow（GenerationRequest.pose_mode）
POSE_WORKFLOWS: dict[str, str] = {"precise": "qwen_edit_pose_cutout", "fast": "sdxl_pose_cutout"}
UPLOAD_SUBFOLDER = "coloring_books"

# 頁面生成的固定參數（Phase 1 實驗結論）
PAGE_FIXED = {
    "ipadapter_weight": 0.55,
    "ipadapter_end_at": 0.6,
    "controlnet_type": "hed/pidi/scribble/ted",
    "controlnet_end_percent": 0.5,
}


def page_params(req: GenerationRequest, character_ref: str, structure_ref: str) -> dict:
    """頁面 workflow 的邏輯參數；沒有結構圖時 ControlNet 強度為 0。"""
    return {
        **PAGE_FIXED,
        "prompt": req.prompt,
        "negative_prompt": req.negative_prompt,
        "seed": req.seed,
        "width": req.width,
        "height": req.height,
        "character_image": character_ref,
        "structure_image": structure_ref,
        "controlnet_strength": req.structure_strength if req.structure_image else 0.0,
    }


def _white_png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (width, height), 255).save(buf, format="PNG")
    return buf.getvalue()


class ComfyEngine:
    def __init__(self, client_factory: Callable[[], ComfyClient], registry: WorkflowRegistry,
                 run_timeout_s: float):
        self.client_factory = client_factory
        self.registry = registry
        self.run_timeout_s = run_timeout_s
        self._upload_cache: dict[str, str] = {}  # 內容 hash 名稱 → ComfyUI 中的名稱

    async def _upload(self, client: ComfyClient, data: bytes, suffix: str) -> str:
        name = f"{hashlib.sha256(data).hexdigest()[:12]}_{suffix}.png"
        if name not in self._upload_cache:
            res = await client.upload_image(data, name, subfolder=UPLOAD_SUBFOLDER)
            self._upload_cache[name] = client.uploaded_ref(res)
        return self._upload_cache[name]

    async def generate(self, req: GenerationRequest, on_progress: ProgressFn | None = None) -> GenerationResult:
        try:
            return await self._generate(req, on_progress)
        except ComfyConnectionError as e:
            raise EngineUnavailable(f"ComfyUI 無法連線：{e}") from e
        except (ComfyError, ParameterError, WorkflowNotReady) as e:
            raise EngineError(str(e)) from e
        except KeyError as e:  # workflow 不存在
            raise EngineError(str(e.args[0]) if e.args else "workflow 不存在") from e
        except OSError as e:
            raise EngineError(f"讀取圖片失敗：{e}") from e

    async def _generate(self, req: GenerationRequest, on_progress: ProgressFn | None) -> GenerationResult:
        if req.kind == GenKind.PAGE and req.character_image is None:
            raise EngineError("頁面生成需要角色參考圖")
        if req.kind == GenKind.POSE and req.character_image is None:
            raise EngineError("角色動作需要角色參考圖")
        if req.kind == GenKind.POSE:
            if req.pose_mode not in POSE_WORKFLOWS:
                raise EngineError(f"未知的角色動作模式：{req.pose_mode}")
            name = POSE_WORKFLOWS[req.pose_mode]
        elif req.kind == GenKind.BACKGROUND:
            if req.background_style not in BACKGROUND_WORKFLOWS:
                raise EngineError(f"未知的背景風格：{req.background_style}")
            name = BACKGROUND_WORKFLOWS[req.background_style]
        else:
            name = PAGE_WORKFLOW if req.kind == GenKind.PAGE else CHARACTER_WORKFLOW
        entry = self.registry.get(name)

        async with self.client_factory() as client:
            if req.kind == GenKind.PAGE:
                char_ref = await self._upload(client, req.character_image.read_bytes(), "char")
                struct_data = (req.structure_image.read_bytes() if req.structure_image
                               else _white_png(req.width, req.height))
                struct_ref = await self._upload(client, struct_data, "struct")
                values = page_params(req, char_ref, struct_ref)
            elif req.kind == GenKind.BACKGROUND and req.background_style == "simple":
                # Qwen 純文字生圖：沒有結構圖／ControlNet／negative_prompt（steps、cfg、LoRA 用 manifest 預設）
                values = {"prompt": req.prompt, "seed": req.seed, "width": req.width, "height": req.height}
            elif req.kind == GenKind.BACKGROUND:
                struct_data = (req.structure_image.read_bytes() if req.structure_image
                               else _white_png(req.width, req.height))
                struct_ref = await self._upload(client, struct_data, "struct")
                values = {
                    "prompt": req.prompt, "negative_prompt": req.negative_prompt, "seed": req.seed,
                    "width": req.width, "height": req.height, "structure_image": struct_ref,
                    "controlnet_type": PAGE_FIXED["controlnet_type"],
                    "controlnet_end_percent": PAGE_FIXED["controlnet_end_percent"],
                    "controlnet_strength": req.structure_strength if req.structure_image else 0.0,
                }
            elif req.kind == GenKind.POSE and req.pose_mode == "precise":
                # Qwen 指令式編輯：沒有 negative_prompt／IP-Adapter 參數（steps、cfg、LoRA 用 manifest 預設）
                char_ref = await self._upload(client, req.character_image.read_bytes(), "char")
                values = {"prompt": req.prompt, "seed": req.seed, "width": req.width, "height": req.height,
                          "character_image": char_ref}
            elif req.kind == GenKind.POSE:
                char_ref = await self._upload(client, req.character_image.read_bytes(), "char")
                values = {
                    "prompt": req.prompt, "negative_prompt": req.negative_prompt, "seed": req.seed,
                    "width": req.width, "height": req.height, "character_image": char_ref,
                    "ipadapter_weight": PAGE_FIXED["ipadapter_weight"],
                    "ipadapter_end_at": PAGE_FIXED["ipadapter_end_at"],
                }
            else:
                values = {"prompt": req.prompt, "negative_prompt": req.negative_prompt, "seed": req.seed,
                          "width": req.width, "height": req.height}
            built = entry.build_prompt(values)

            def progress(ev: ProgressEvent) -> None:
                if on_progress and ev.type == "progress" and ev.value is not None and ev.max is not None:
                    on_progress(ev.value, ev.max)

            started = time.monotonic()
            result = await client.run(built.prompt, timeout_s=self.run_timeout_s, on_progress=progress)
            files = [f for f in result.files if f.node_id in built.output_node_ids and f.type == "output"]
            if not files:
                raise EngineError("ComfyUI 沒有輸出圖片")
            # manifest outputs 依序對應 output_node_ids；用 title 決定每個輸出的用途
            titles = {nid: o["title"] for nid, o in zip(built.output_node_ids, entry.manifest["outputs"])}
            by_title = {}
            for f in files:
                by_title.setdefault(titles[f.node_id], f)
            main = by_title.get("OUTPUT", files[0])
            image = await client.download(main)
            extra: dict[str, bytes] = {}
            if "MASK" in by_title:
                extra["mask"] = await client.download(by_title["MASK"])
            elif req.kind == GenKind.POSE:
                raise EngineError("去背失敗：沒有遮罩輸出")
            params = dict(built.params)
            if req.kind in (GenKind.POSE, GenKind.BACKGROUND) and req.variation:
                params["variation"] = req.variation  # 已併入 prompt，另外記錄方便查詢
            return GenerationResult(
                image=image, elapsed_s=time.monotonic() - started, engine="comfyui",
                workflow_name=built.workflow_name, workflow_version=built.workflow_version,
                workflow_hash=built.workflow_hash, models=built.models, params=params,
                extra_images=extra,
            )
