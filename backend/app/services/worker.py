"""單一背景生成 worker：依 plan_order 逐筆執行 QUEUED 的 Generation（單 GPU，絕不並行）。

同模型家族（SDXL / Qwen）的任務優先，等待過久的任務最先；見 app.services.scheduling。
「等待過久」以 plan_clock（上一次取任務的時間）判斷，所以任務執行中顯示的順位不會變，
下一次取任務也用同一個時間，與顯示一致；佇列清空（閒置）後改用現在時間。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime
from enum import Enum

from sqlmodel import Session, col, select, update

from app.models import Generation, GenKind, GenStatus, utcnow
from app.services.engine import (
    RETIRED,
    EngineError,
    EngineUnavailable,
    GenerationEngine,
    GenerationRequest,
)
from app.services.autoselect import autoselect
from app.services.compose import largest_component
from app.services.scheduling import QueuedJob, family_of, plan_order
from app.storage import Storage

log = logging.getLogger(__name__)


class WorkerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    STOPPED = "stopped"


# request_json 的核心欄位（extra 不能蓋掉）
_CORE_KEYS = frozenset({"kind", "prompt", "negative_prompt", "seed", "width", "height", "character_image",
                        "variation"})


def encode_request(
    *,
    kind: GenKind,
    prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    character_image: str | None = None,
    variation: str | None = None,
    extra: dict | None = None,
) -> str:
    """產生 Generation.request_json；character_image 為 storage 相對路徑，variation 一律寫入（可為 null）。
    extra 併入 JSON，只當來源紀錄（例如 pose_mode、background_style）；引擎不讀，不能蓋掉核心欄位。"""
    d = {
        "kind": GenKind(kind).value,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "character_image": character_image,
        "variation": variation,
    }
    if extra:
        clash = _CORE_KEYS & set(extra)
        if clash:
            raise ValueError(f"extra 不能覆寫核心欄位：{', '.join(sorted(clash))}")
        d.update(extra)
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def _is_retired(d: dict) -> bool:
    """需要已停用路線的請求：舊整頁流程，或明確要求 SDXL 的快速動作／細緻背景。
    沒有 pose_mode／background_style 的列照 Qwen 執行（新列只把它們當來源紀錄）。"""
    return d.get("kind") == GenKind.PAGE.value or d.get("pose_mode") == "fast" \
        or d.get("background_style") == "detailed"


def decode_request(text: str, storage: Storage) -> GenerationRequest:
    """還原 GenerationRequest；相對路徑轉絕對路徑，超出 storage 根目錄會丟 ValueError。
    需要已停用路線的請求丟 ValueError(RETIRED)。舊列多出的 structure_* 等欄位忽略。"""
    d = json.loads(text)
    if _is_retired(d):
        raise ValueError(RETIRED)
    rel = d.get("character_image")
    return GenerationRequest(
        kind=GenKind(d["kind"]),
        prompt=d["prompt"],
        negative_prompt=d["negative_prompt"],
        seed=d["seed"],
        width=d["width"],
        height=d["height"],
        character_image=None if rel is None else storage.abs(rel),
        variation=d.get("variation"),
    )


class GenerationWorker:
    def __init__(
        self,
        engine: GenerationEngine,
        sessions: Callable[[], Session],
        storage: Storage,
        retry_backoff_s: float,
        max_wait_s: float = 180.0,
    ) -> None:
        self.engine = engine
        self.sessions = sessions
        self.storage = storage
        self.retry_backoff_s = retry_backoff_s
        self.max_wait_s = max_wait_s
        # 上一個取走的任務的模型家族（只存在記憶體；重啟後為 None → 最舊優先）
        self.last_family: str | None = None
        # 上一次取任務的判斷時間；None 表示閒置（佇列清空後）→ 以現在時間判斷
        self.plan_clock: datetime | None = None
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
            order = self._plan(s)
            if gen_id not in order:  # 讀取之間狀態剛好改變
                return None
            running = s.exec(select(Generation.id).where(col(Generation.status) == GenStatus.RUNNING)).first()
            return order.index(gen_id) + (1 if running is not None else 0)

    def _plan(self, s: Session) -> list[int]:
        """目前 QUEUED 任務的執行順序（與 _claim 取任務的順序相同）。
        逾時以 plan_clock 判斷（閒置時為現在時間），讓顯示的順位在任務執行中保持穩定。"""
        rows = s.exec(
            select(Generation.id, Generation.kind, Generation.request_json, Generation.created_at)
            .where(col(Generation.status) == GenStatus.QUEUED)
            .order_by(col(Generation.created_at), col(Generation.id))
        ).all()
        queued = [
            QueuedJob(id=gid, family=family_of(kind, rj), created_at=created_at)
            for gid, kind, rj, created_at in rows
        ]
        now = self.plan_clock if self.plan_clock is not None else utcnow()
        return plan_order(queued, self.last_family, now, self.max_wait_s)

    def _claim(self) -> Generation | None:
        """依 plan_order 的第一筆，以單一 UPDATE ... WHERE status=QUEUED 取得任務；
        rowcount 為 0 表示被搶走（或被取消），重試。成功後記錄其模型家族與 plan_clock；
        佇列空時清掉 plan_clock（閒置）。"""
        while True:
            with self.sessions() as s:
                order = self._plan(s)
                if not order:
                    self.plan_clock = None
                    return None
                gen_id = order[0]
                res = s.exec(
                    update(Generation)
                    .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.QUEUED)
                    .values(status=GenStatus.RUNNING, started_at=utcnow())
                )
                s.commit()
                if res.rowcount == 1:
                    gen = s.get(Generation, gen_id)
                    self.last_family = family_of(gen.kind, gen.request_json)
                    self.plan_clock = utcnow()
                    return gen

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
            mask_rel = None
            if result.extra_images.get("mask"):
                mask_rel = self.storage.generation_mask(gen.book_id, gen_id)
                mask_png = result.extra_images["mask"]
                if req.kind == GenKind.POSE:
                    mask_png = largest_component(mask_png)  # 丟掉與角色分離的陰影
                self.storage.write(mask_rel, mask_png)
            self._finish(
                gen_id,
                mask_path=mask_rel,
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
            with self.sessions() as s:
                done = s.get(Generation, gen_id)
                if done is not None:
                    autoselect(s, done)
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
