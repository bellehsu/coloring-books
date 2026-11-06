"""測試用引擎替身：可回傳 PNG、丟 EngineError / EngineUnavailable，並記錄收到的請求。"""

from __future__ import annotations

import io
from collections import deque

from PIL import Image

from app.services.engine import GenerationRequest, GenerationResult, ProgressFn


def make_png(size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


class FakeEngine:
    """outcomes 是依序消耗的結果佇列：Exception 實例會被丟出，其他值忽略；佇列空了就成功。
    也可設定 `error` 屬性（持續丟出，直到設回 None）。"""

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []
        self.outcomes: deque[Exception | None] = deque()
        self.error: Exception | None = None
        self.progress_steps: list[tuple[int, int]] = []
        self.image: bytes = make_png()
        self.delay_s: float = 0.0

    async def generate(self, req: GenerationRequest, on_progress: ProgressFn | None = None) -> GenerationResult:
        import asyncio

        self.requests.append(req)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if on_progress:
            for value, maximum in self.progress_steps:
                on_progress(value, maximum)
        outcome = self.outcomes.popleft() if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if self.error is not None:
            raise self.error
        return GenerationResult(
            image=self.image,
            elapsed_s=0.25,
            engine="fake",
            workflow_name="fake_wf",
            workflow_version="abc123",
            workflow_hash="abc123def456",
            models=["m1.safetensors", "m2.gguf"],
            params={"seed": req.seed, "prompt": req.prompt, "標題": "中文"},
        )
