"""以 ComfyUI 實作的生成引擎：把 GenerationRequest 對應到 workflow manifest 的白名單參數。

Phase 9 只有三條 Qwen-Image-Edit-2511 路線（spec 2026-11-10 §4.7）。其他種類（舊整頁流程 PAGE 等）
一律丟 EngineError(RETIRED)，絕不偷換模型。steps、cfg、LoRA 等用 manifest 預設；Qwen 沒有 negative_prompt。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

from app.models import GenKind
from app.services.comfy_client import ComfyClient, ComfyConnectionError, ComfyError, ProgressEvent
from app.services.engine import (
    RETIRED, EngineError, EngineUnavailable, GenerationRequest, GenerationResult, ProgressFn,
)
from app.services.workflow_registry import ParameterError, WorkflowNotReady, WorkflowRegistry

__all__ = ["RETIRED", "WORKFLOWS", "UPLOAD_SUBFOLDER", "ComfyEngine"]

WORKFLOWS: dict[GenKind, str] = {
    GenKind.CHARACTER: "qwen_character",
    GenKind.BACKGROUND: "qwen_background",
    GenKind.POSE: "qwen_edit_pose_cutout",
}
UPLOAD_SUBFOLDER = "coloring_books"


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
        name = WORKFLOWS.get(req.kind)
        if name is None:
            raise EngineError(RETIRED)
        if req.kind == GenKind.POSE and req.character_image is None:
            raise EngineError("角色動作需要角色參考圖")
        entry = self.registry.get(name)

        async with self.client_factory() as client:
            values: dict = {"prompt": req.prompt, "seed": req.seed, "width": req.width, "height": req.height}
            if req.kind == GenKind.POSE:
                values["character_image"] = await self._upload(client, req.character_image.read_bytes(), "char")
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
            if req.variation:
                params["variation"] = req.variation  # 已併入 prompt，另外記錄方便查詢
            return GenerationResult(
                image=image, elapsed_s=time.monotonic() - started, engine="comfyui",
                workflow_name=built.workflow_name, workflow_version=built.workflow_version,
                workflow_hash=built.workflow_hash, models=built.models, params=params,
                extra_images=extra,
            )
