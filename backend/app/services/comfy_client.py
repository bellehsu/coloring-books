"""ComfyUI 非同步客戶端（HTTP + WebSocket）。

只透過 ComfyUI 的公開 API 溝通，不 import ComfyUI 原始碼。
測試時以 httpx.MockTransport 與假的 ws_connect 注入，不需要 GPU 或執行中的 ComfyUI。

WebSocket 訊息（ComfyUI 0.3x）：
  status / execution_start / execution_cached / executing{node, prompt_id}
  progress{value, max, node, prompt_id} / executed{node, output, prompt_id}
  execution_success / execution_error{node_id, node_type, exception_message, traceback}
  execution_interrupted
  executing 且 node 為 None 代表該 prompt 結束（舊版沒有 execution_success 時的完成訊號）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx


class ComfyError(Exception):
    """所有 ComfyUI 相關錯誤的基底類別。"""


class ComfyConnectionError(ComfyError):
    """連線層失敗（連不上、連線逾時、連線池逾時、對方中途斷線）。HTTP 4xx/5xx 不算，那是一般 ComfyError。"""


_TRANSPORT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)


class ComfyPromptError(ComfyError):
    """POST /prompt 被拒（驗證失敗）。node_errors 為 ComfyUI 原樣回傳的節點錯誤。"""

    def __init__(self, message: str, error: Any = None, node_errors: dict | None = None):
        self.error = error
        self.node_errors = node_errors or {}
        super().__init__(message)


class ComfyExecutionError(ComfyError):
    """執行期間節點拋出例外或被中斷。"""

    def __init__(self, message: str, *, prompt_id: str, node_id: str | None = None,
                 node_type: str | None = None, traceback: list[str] | None = None):
        self.prompt_id = prompt_id
        self.node_id = node_id
        self.node_type = node_type
        self.traceback = traceback or []
        super().__init__(message)


class ComfyTimeoutError(ComfyError):
    pass


@dataclass
class ProgressEvent:
    prompt_id: str
    type: str  # executing | progress | executed | cached | ...
    node: str | None = None
    value: int | None = None
    max: int | None = None
    data: dict = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], Awaitable[None] | None]


@dataclass
class OutputFile:
    node_id: str
    filename: str
    subfolder: str
    type: str  # output | temp


@dataclass
class RunResult:
    prompt_id: str
    outputs: dict[str, Any]
    files: list[OutputFile]
    elapsed_s: float
    status: dict[str, Any]


class WebSocketLike(Protocol):
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


WsConnect = Callable[[str], Awaitable[WebSocketLike]]


async def _default_ws_connect(url: str) -> WebSocketLike:
    import websockets

    # ComfyUI 會推送預覽圖等大型二進位訊息
    return await websockets.connect(url, max_size=None, open_timeout=10)


def _files_from_outputs(outputs: dict[str, Any]) -> list[OutputFile]:
    files: list[OutputFile] = []
    for node_id, node_out in outputs.items():
        for key in ("images", "gifs"):
            for item in node_out.get(key, []) or []:
                if isinstance(item, dict) and "filename" in item:
                    files.append(OutputFile(node_id=str(node_id), filename=item["filename"],
                                            subfolder=item.get("subfolder", ""), type=item.get("type", "output")))
    return files


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        *,
        request_timeout_s: float = 30.0,
        client_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        ws_connect: WsConnect | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or uuid.uuid4().hex
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=request_timeout_s, transport=transport)
        self._ws_connect = ws_connect or _default_ws_connect

    @property
    def ws_url(self) -> str:
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        host = self.base_url.split("://", 1)[1]
        return f"{scheme}://{host}/ws?clientId={self.client_id}"

    async def __aenter__(self) -> "ComfyClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- HTTP 基本操作 -------------------------------------------------------

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kw)
        except _TRANSPORT_ERRORS as e:  # 只有連線層失敗才算「連不上」（會重排）；ConnectTimeout 是 TimeoutException 的子類，要先判斷
            raise ComfyConnectionError(f"無法連線 ComfyUI（{self.base_url}）：{e}") from e
        except httpx.TimeoutException as e:
            raise ComfyTimeoutError(f"{method} {path} 逾時") from e
        except httpx.HTTPError as e:
            raise ComfyError(f"{method} {path} 失敗：{type(e).__name__}: {e}") from e

    async def _get_json(self, path: str, **kw: Any) -> Any:
        resp = await self._request("GET", path, **kw)
        if resp.status_code != 200:
            raise ComfyError(f"GET {path} 回傳 HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise ComfyConnectionError(f"GET {path} 回傳的不是 JSON（這個位址可能不是 ComfyUI）") from e

    async def system_stats(self) -> dict:
        data = await self._get_json("/system_stats")
        if not isinstance(data, dict) or "system" not in data:
            raise ComfyConnectionError(f"{self.base_url}/system_stats 格式不符，這個位址可能不是 ComfyUI")
        return data

    async def object_info(self) -> dict[str, Any]:
        return await self._get_json("/object_info")

    async def model_folders(self) -> list[str]:
        return await self._get_json("/models")

    async def list_models(self, folder: str) -> list[str] | None:
        """回傳資料夾類型下的模型檔名；資料夾類型未註冊（ComfyUI 回 404）時回傳 None。"""
        resp = await self._request("GET", f"/models/{folder}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise ComfyError(f"GET /models/{folder} 回傳 HTTP {resp.status_code}")
        return resp.json()

    async def upload_image(
        self,
        data: bytes | Path,
        filename: str | None = None,
        *,
        subfolder: str = "",
        overwrite: bool = True,
        image_type: str = "input",
    ) -> dict[str, str]:
        """上傳圖片到 ComfyUI input 資料夾。回傳 {"name","subfolder","type"}。

        送入 LoadImage 時使用的值為 "subfolder/name"（subfolder 為空則只有 name），見 uploaded_ref()。
        """
        if isinstance(data, Path):
            filename = filename or data.name
            data = data.read_bytes()
        if not filename:
            raise ValueError("上傳 bytes 時必須提供 filename")
        form = {"type": image_type, "overwrite": "true" if overwrite else "false"}
        if subfolder:
            form["subfolder"] = subfolder
        resp = await self._request(
            "POST", "/upload/image", data=form, files={"image": (filename, data, "application/octet-stream")}
        )
        if resp.status_code != 200:
            raise ComfyError(f"上傳 {filename} 失敗：HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json()

    @staticmethod
    def uploaded_ref(upload_result: dict[str, str]) -> str:
        sub = upload_result.get("subfolder") or ""
        return f"{sub}/{upload_result['name']}" if sub else upload_result["name"]

    async def queue_prompt(self, prompt: dict[str, Any], *, extra_data: dict | None = None) -> str:
        body: dict[str, Any] = {"prompt": prompt, "client_id": self.client_id}
        if extra_data:
            body["extra_data"] = extra_data
        resp = await self._request("POST", "/prompt", json=body)
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            payload = {"error": resp.text[:500]}
        if resp.status_code != 200:
            err = payload.get("error")
            msg = err.get("message") if isinstance(err, dict) else str(err)
            details = err.get("details") if isinstance(err, dict) else None
            node_errors = payload.get("node_errors") or {}
            summary = "; ".join(
                f"node {nid} ({ne.get('class_type')}): "
                + ", ".join(f"{e.get('message')} {e.get('details', '')}".strip() for e in ne.get("errors", []))
                for nid, ne in node_errors.items()
            )
            raise ComfyPromptError(
                f"ComfyUI 拒絕 prompt：{msg}" + (f"（{details}）" if details else "") + (f" — {summary}" if summary else ""),
                error=err, node_errors=node_errors,
            )
        if payload.get("node_errors"):
            raise ComfyPromptError("ComfyUI 回報節點錯誤", node_errors=payload["node_errors"])
        return payload["prompt_id"]

    async def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """回傳該 prompt 的 history 項目；尚未完成時回傳 None。"""
        data = await self._get_json(f"/history/{prompt_id}")
        return data.get(prompt_id)

    async def download(self, f: OutputFile | dict[str, str]) -> bytes:
        if isinstance(f, OutputFile):
            params = {"filename": f.filename, "subfolder": f.subfolder, "type": f.type}
        else:
            params = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
        resp = await self._request("GET", "/view", params=params)
        if resp.status_code != 200:
            raise ComfyError(f"下載 {params['filename']} 失敗：HTTP {resp.status_code}")
        return resp.content

    async def interrupt(self) -> None:
        await self._request("POST", "/interrupt")

    async def free_memory(self, unload_models: bool = True) -> None:
        await self._request("POST", "/free", json={"unload_models": unload_models, "free_memory": True})

    # ---- 執行與等待 ------------------------------------------------------------

    async def _wait_ws(self, ws: WebSocketLike, prompt_id: str, on_progress: ProgressCallback | None) -> None:
        async def emit(ev: ProgressEvent) -> None:
            if on_progress is not None:
                res = on_progress(ev)
                if asyncio.iscoroutine(res):
                    await res

        while True:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                continue  # 預覽圖
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            data = msg.get("data") or {}
            if data.get("prompt_id") not in (None, prompt_id):
                continue
            if mtype == "status" or "prompt_id" not in data:
                continue
            if mtype == "executing":
                if data.get("node") is None:
                    return
                await emit(ProgressEvent(prompt_id, "executing", node=str(data["node"]), data=data))
            elif mtype == "progress":
                await emit(ProgressEvent(prompt_id, "progress", node=data.get("node"),
                                         value=data.get("value"), max=data.get("max"), data=data))
            elif mtype == "executed":
                await emit(ProgressEvent(prompt_id, "executed", node=str(data.get("node")), data=data))
            elif mtype == "execution_cached":
                await emit(ProgressEvent(prompt_id, "cached", data=data))
            elif mtype == "execution_success":
                return
            elif mtype == "execution_error":
                raise ComfyExecutionError(
                    f"節點 {data.get('node_id')}（{data.get('node_type')}）執行失敗："
                    f"{data.get('exception_type', '')}: {data.get('exception_message', '').strip()}",
                    prompt_id=prompt_id, node_id=data.get("node_id"), node_type=data.get("node_type"),
                    traceback=data.get("traceback"),
                )
            elif mtype == "execution_interrupted":
                raise ComfyExecutionError("執行被中斷", prompt_id=prompt_id, node_id=data.get("node_id"),
                                          node_type=data.get("node_type"))

    async def _wait_poll(self, prompt_id: str, poll_interval_s: float) -> None:
        while True:
            if await self.get_history(prompt_id) is not None:
                return
            await asyncio.sleep(poll_interval_s)

    async def run(
        self,
        prompt: dict[str, Any],
        *,
        timeout_s: float = 900.0,
        on_progress: ProgressCallback | None = None,
        poll_interval_s: float = 1.0,
    ) -> RunResult:
        """送出 prompt 並等待完成，回傳輸出檔案清單。

        先連 WebSocket 再送 prompt，避免漏掉事件；WebSocket 連不上時退回輪詢 /history。
        逾時會呼叫 /interrupt 並拋出 ComfyTimeoutError。
        """
        started = time.monotonic()
        ws: WebSocketLike | None = None
        try:
            ws = await self._ws_connect(self.ws_url)
        except Exception:  # noqa: BLE001 - WS 失敗時改用輪詢
            ws = None
        try:
            prompt_id = await self.queue_prompt(prompt)
            try:
                waiter = self._wait_ws(ws, prompt_id, on_progress) if ws else self._wait_poll(prompt_id, poll_interval_s)
                await asyncio.wait_for(waiter, timeout=timeout_s)
            except asyncio.TimeoutError as e:
                try:
                    await self.interrupt()
                except ComfyError:
                    pass
                raise ComfyTimeoutError(f"prompt {prompt_id} 超過 {timeout_s:.0f} 秒未完成，已送出中斷") from e
            except ComfyExecutionError:
                raise
            except Exception as e:  # noqa: BLE001 - WS 中途斷線，改輪詢剩餘時間
                if isinstance(e, ComfyError):
                    raise
                remaining = max(1.0, timeout_s - (time.monotonic() - started))
                try:
                    await asyncio.wait_for(self._wait_poll(prompt_id, poll_interval_s), timeout=remaining)
                except asyncio.TimeoutError as te:
                    raise ComfyTimeoutError(f"prompt {prompt_id} 等待逾時（WebSocket 中斷後輪詢）") from te
        finally:
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass

        # history 可能比 WS 完成事件稍晚寫入
        entry = None
        for _ in range(20):
            entry = await self.get_history(prompt_id)
            if entry is not None:
                break
            await asyncio.sleep(min(poll_interval_s, 0.25))
        if entry is None:
            raise ComfyExecutionError("執行結束但 history 沒有紀錄", prompt_id=prompt_id)
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            err = next((m[1] for m in status.get("messages", []) if m and m[0] == "execution_error"), {})
            raise ComfyExecutionError(
                f"節點 {err.get('node_id')}（{err.get('node_type')}）執行失敗：{err.get('exception_message', '未知錯誤')}",
                prompt_id=prompt_id, node_id=err.get("node_id"), node_type=err.get("node_type"),
                traceback=err.get("traceback"),
            )
        outputs = entry.get("outputs") or {}
        return RunResult(prompt_id=prompt_id, outputs=outputs, files=_files_from_outputs(outputs),
                         elapsed_s=time.monotonic() - started, status=status)
