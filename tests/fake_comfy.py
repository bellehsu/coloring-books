"""測試用的假 ComfyUI：httpx MockTransport + 假 WebSocket，不需要 GPU。"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from typing import Any

import httpx
import numpy as np
from PIL import Image

from app.services.comfy_client import ComfyClient


def png_bytes(arr: np.ndarray | None = None) -> bytes:
    if arr is None:
        arr = np.full((64, 48), 255, np.uint8)
        arr[10:54, 20:23] = 0
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


class FakeWS:
    def __init__(self, comfy: "FakeComfy"):
        self.comfy = comfy
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        comfy.sockets.append(self)

    async def recv(self):
        item = await self.queue.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


class FakeComfy:
    """
    mode:
      "ok"       正常完成，輸出一張圖
      "error"    WebSocket 送 execution_error
      "reject"   POST /prompt 回 400 + node_errors
      "hang"     永遠不完成（測逾時）
      "ws_drop"  WebSocket 中途斷線，之後 history 才出現
    """

    def __init__(self, object_info: dict | None = None, models: dict[str, list[str]] | None = None,
                 mode: str = "ok", image: bytes | None = None, ws_fails: bool = False):
        self.object_info = object_info or {}
        self.models = models or {}
        self.mode = mode
        self.image = image or png_bytes()
        self.ws_fails = ws_fails
        self.sockets: list[FakeWS] = []
        self.prompts: list[dict] = []
        self.uploads: list[dict] = []
        self.history: dict[str, Any] = {}
        self.interrupted = False
        self.freed = 0
        self.dead = False  # True：模擬 ComfyUI 程序已停止，所有連線失敗

    # ---- WebSocket ----
    async def ws_connect(self, url: str) -> FakeWS:
        if self.ws_fails:
            raise OSError("ws refused")
        return FakeWS(self)

    def _emit(self, msg: dict | bytes | Exception) -> None:
        for s in self.sockets:
            if not s.closed:
                s.queue.put_nowait(msg if isinstance(msg, (bytes, Exception)) else json.dumps(msg))

    def _complete(self, pid: str, prompt: dict) -> None:
        out_node = next((nid for nid, n in prompt.items() if n["class_type"] == "SaveImage"), "9")
        self.history[pid] = {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {out_node: {"images": [{"filename": f"{pid}.png", "subfolder": "", "type": "output"}]}},
        }

    def _run(self, pid: str, prompt: dict) -> None:
        if self.mode == "hang":
            return
        self._emit({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}})
        self._emit({"type": "execution_start", "data": {"prompt_id": pid}})
        self._emit({"type": "executing", "data": {"node": "1", "prompt_id": pid}})
        self._emit(b"\x00\x00\x00\x01preview")
        self._emit({"type": "progress", "data": {"value": 1, "max": 2, "node": "5", "prompt_id": pid}})
        self._emit({"type": "progress", "data": {"value": 1, "max": 2, "prompt_id": "someone-else"}})
        if self.mode == "error":
            self.history[pid] = {"status": {"status_str": "error", "messages": []}, "outputs": {}}
            self._emit({"type": "execution_error", "data": {
                "prompt_id": pid, "node_id": "5", "node_type": "KSampler", "exception_type": "RuntimeError",
                "exception_message": "CUDA out of memory", "traceback": ["tb"]}})
            return
        if self.mode == "ws_drop":
            self._emit(ConnectionError("socket closed"))
            asyncio.get_event_loop().call_later(0.05, self._complete, pid, prompt)
            return
        self._emit({"type": "progress", "data": {"value": 2, "max": 2, "node": "5", "prompt_id": pid}})
        self._complete(pid, prompt)
        self._emit({"type": "executed", "data": {"node": "9", "output": {}, "prompt_id": pid}})
        self._emit({"type": "execution_success", "data": {"prompt_id": pid}})

    # ---- HTTP ----
    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.dead:
            raise httpx.ConnectError("All connection attempts failed")
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.36.0"},
                                             "devices": [{"type": "cuda", "vram_total": 17e9}]})
        if path == "/object_info":
            return httpx.Response(200, json=self.object_info)
        if path == "/models":
            return httpx.Response(200, json=list(self.models))
        if path.startswith("/models/"):
            folder = path.split("/", 2)[2]
            if folder not in self.models:
                return httpx.Response(404)
            return httpx.Response(200, json=self.models[folder])
        if path == "/upload/image":
            body = request.content
            name = body.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
            sub = "coloring_books" if b'name="subfolder"' in body else ""
            self.uploads.append({"name": name, "subfolder": sub})
            return httpx.Response(200, json={"name": name, "subfolder": sub, "type": "input"})
        if path == "/prompt":
            body = json.loads(request.content)
            if self.mode == "reject":
                return httpx.Response(400, json={
                    "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation",
                              "details": ""},
                    "node_errors": {"5": {"class_type": "KSampler", "errors": [
                        {"message": "Value not in list", "details": "sampler_name: 'bogus' not in [...]"}]}}})
            pid = uuid.uuid4().hex
            self.prompts.append(body)
            asyncio.get_event_loop().call_soon(self._run, pid, body["prompt"])
            return httpx.Response(200, json={"prompt_id": pid, "number": 1, "node_errors": {}})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: self.history[pid]} if pid in self.history else {})
        if path == "/view":
            return httpx.Response(200, content=self.image)
        if path == "/interrupt":
            self.interrupted = True
            return httpx.Response(200)
        if path == "/free":
            self.freed += 1
            return httpx.Response(200)
        return httpx.Response(404)

    def client(self, **kw) -> ComfyClient:
        return ComfyClient("http://comfy.test:8188", transport=httpx.MockTransport(self.handler),
                           ws_connect=self.ws_connect, **kw)
