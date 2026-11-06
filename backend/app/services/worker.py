"""單一背景生成 worker：依 id 順序逐筆執行 QUEUED 的 Generation（單 GPU，絕不並行）。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from enum import Enum

from sqlmodel import Session, col, select, update

from app.models import Generation, GenKind, GenStatus, utcnow
from app.services.engine import (
    EngineError,
    EngineUnavailable,
    GenerationEngine,
    GenerationRequest,
)
from app.storage import Storage

log = logging.getLogger(__name__)


class WorkerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    STOPPED = "stopped"


def encode_request(
    *,
    kind: GenKind,
    prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    character_image: str | None = None,
    structure_image: str | None = None,
    structure_strength: float = 0.0,
) -> str:
    """產生 Generation.request_json；圖片欄位為 storage 相對路徑。"""
    return json.dumps(
        {
            "kind": GenKind(kind).value,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "width": width,
            "height": height,
            "character_image": character_image,
            "structure_image": structure_image,
            "structure_strength": structure_strength,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def decode_request(text: str, storage: Storage) -> GenerationRequest:
    """還原 GenerationRequest；相對路徑轉絕對路徑，超出 storage 根目錄會丟 ValueError。"""
    d = json.loads(text)

    def _abs(rel):
        return None if rel is None else storage.abs(rel)

    return GenerationRequest(
        kind=GenKind(d["kind"]),
        prompt=d["prompt"],
        negative_prompt=d["negative_prompt"],
        seed=d["seed"],
        width=d["width"],
        height=d["height"],
        character_image=_abs(d.get("character_image")),
        structure_image=_abs(d.get("structure_image")),
        structure_strength=d.get("structure_strength", 0.0),
    )


class GenerationWorker:
    def __init__(
        self,
        engine: GenerationEngine,
        sessions: Callable[[], Session],
        storage: Storage,
        retry_backoff_s: float,
    ) -> None:
        self.engine = engine
        self.sessions = sessions
        self.storage = storage
        self.retry_backoff_s = retry_backoff_s
        self.state = WorkerState.STOPPED
        self.last_error: str | None = None
        self.progress: dict[int, tuple[int, int]] = {}
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

    # ---- 生命週期 ----
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.requeue_interrupted()
        self.state = WorkerState.IDLE
        self._event_loop = asyncio.get_running_loop()
        self._wakeup.set()
        self._task = asyncio.create_task(self._loop(), name="generation-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state = WorkerState.STOPPED

    def notify(self) -> None:
        """喚醒背景迴圈；可從其他執行緒（例如 FastAPI 的同步 handler）安全呼叫。"""
        loop = self._event_loop
        if loop is not None and not loop.is_closed():
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False
            if not on_loop:
                loop.call_soon_threadsafe(self._wakeup.set)
                return
        self._wakeup.set()

    # ---- 資料庫操作（同步，每次新 session）----
    def requeue_interrupted(self) -> int:
        with self.sessions() as s:
            res = s.exec(
                update(Generation)
                .where(col(Generation.status) == GenStatus.RUNNING)
                .values(status=GenStatus.QUEUED, started_at=None)
            )
            s.commit()
            return res.rowcount

    def queue_position(self, gen_id: int) -> int | None:
        with self.sessions() as s:
            gen = s.get(Generation, gen_id)
            if gen is None:
                return None
            if gen.status == GenStatus.RUNNING:
                return 0
            if gen.status != GenStatus.QUEUED:
                return None
            ahead = s.exec(
                select(Generation.id).where(
                    col(Generation.status) == GenStatus.QUEUED, col(Generation.id) < gen_id
                )
            ).all()
            running = s.exec(select(Generation.id).where(col(Generation.status) == GenStatus.RUNNING)).first()
            return len(ahead) + (1 if running is not None else 0)

    def _claim(self) -> Generation | None:
        """單一 UPDATE ... WHERE status=QUEUED 取得最小 id 的任務；rowcount 為 0 表示被搶走，重試。"""
        while True:
            with self.sessions() as s:
                gen_id = s.exec(
                    select(Generation.id)
                    .where(col(Generation.status) == GenStatus.QUEUED)
                    .order_by(col(Generation.id))
                ).first()
                if gen_id is None:
                    return None
                res = s.exec(
                    update(Generation)
                    .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.QUEUED)
                    .values(status=GenStatus.RUNNING, started_at=utcnow())
                )
                s.commit()
                if res.rowcount == 1:
                    return s.get(Generation, gen_id)

    def _finish(self, gen_id: int, **values) -> None:
        """只更新仍為 RUNNING 的列（執行中被改成 CANCELLED 等的不覆蓋）。"""
        with self.sessions() as s:
            s.exec(
                update(Generation)
                .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.RUNNING)
                .values(**values)
            )
            s.commit()

    # ---- 執行 ----
    async def run_once(self) -> bool:
        gen = self._claim()
        if gen is None:
            return False
        gen_id = gen.id
        self.state = WorkerState.RUNNING
        try:
            try:
                req = decode_request(gen.request_json, self.storage)
            except Exception as e:  # noqa: BLE001
                self._fail(gen_id, f"無法解析請求：{type(e).__name__}: {e}")
                return True
            result = await self.engine.generate(
                req, lambda value, maximum: self.progress.__setitem__(gen_id, (value, maximum))
            )
            rel = self.storage.generation_image(gen.book_id, gen_id)
            self.storage.write(rel, result.image)
            self._finish(
                gen_id,
                status=GenStatus.SUCCEEDED,
                image_path=rel,
                engine=result.engine,
                workflow_name=result.workflow_name,
                workflow_version=result.workflow_version,
                workflow_hash=result.workflow_hash,
                models_json=json.dumps(result.models, ensure_ascii=False),
                params_json=json.dumps(result.params, ensure_ascii=False, sort_keys=True),
                elapsed_s=result.elapsed_s,
                error=None,
                finished_at=utcnow(),
            )
            self.last_error = None
            self.state = WorkerState.IDLE
        except EngineUnavailable as e:
            self._finish(gen_id, status=GenStatus.QUEUED, started_at=None)
            self.last_error = str(e)
            self.state = WorkerState.ENGINE_UNAVAILABLE
        except EngineError as e:
            self._fail(gen_id, str(e))
        except Exception as e:  # noqa: BLE001  單筆任務的任何例外都不可殺死 worker
            log.exception("generation %s crashed", gen_id)
            self._fail(gen_id, f"非預期錯誤：{type(e).__name__}: {e}")
        finally:
            self.progress.pop(gen_id, None)
        return True

    def _fail(self, gen_id: int, message: str) -> None:
        self._finish(gen_id, status=GenStatus.FAILED, error=message, finished_at=utcnow())
        self.last_error = message
        self.state = WorkerState.IDLE

    async def _loop(self) -> None:
        while True:
            # 先清除再取任務：任務執行中到達的 notify() 會讓事件保持 set，不會遺失
            self._wakeup.clear()
            try:
                did = await self.run_once()
            except Exception:  # noqa: BLE001  例如資料庫暫時錯誤；退避後繼續
                log.exception("worker loop error")
                self.state = WorkerState.IDLE
                await self._wait(self.retry_backoff_s)
                continue
            if not did:
                # 佇列已空：不再有待重試的任務，回到 IDLE 並無限期等待 notify()
                self.state = WorkerState.IDLE
                self.last_error = None
                await self._wakeup.wait()
            elif self.state == WorkerState.ENGINE_UNAVAILABLE:
                await self._wait(self.retry_backoff_s)

    async def _wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout)
        except asyncio.TimeoutError:
            pass
