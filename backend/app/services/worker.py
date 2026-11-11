"""單一背景生成 worker：依建立時間（最舊的先）逐筆執行 QUEUED 的 Generation（單 GPU，絕不並行）。

每做完一張生成（以及閒置時），先把排隊的頁面流程（page_task，services/pipeline.py：CPU 工作）做完，
再做下一張生成。頁面的背景／角色動作生成成功時自動選用並排這頁的流程；失敗時頁面 attention="failed"。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from enum import Enum

from sqlalchemy import or_
from sqlmodel import Session, col, select, update

from app.models import Generation, GenKind, GenStatus, PageTaskStatus, utcnow
from app.services.engine import (
    RETIRED,
    EngineError,
    EngineUnavailable,
    GenerationEngine,
    GenerationRequest,
)
from app.services import pipeline
from app.services.compose import largest_component
from app.services.usage import refund_generations
from app.storage import Storage, StorageNotFound

log = logging.getLogger(__name__)


class WorkerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    STOPPED = "stopped"


# 後端在請求裡自己產生的生成紀錄（上傳背景、確認合成）。寫物件期間這一列以 RUNNING、沒有圖暫存
# （api/compose.py：storage I/O 不在寫入交易裡做），它不是 worker 的 GPU 任務：佇列狀態與排隊位置不算它，
# 重啟時也不可放回佇列
LOCAL_ENGINES = ("upload", "compose")
LOCAL_INTERRUPTED = "處理中斷（伺服器重新啟動），請重新上傳或合成"


def worker_job():
    """SQL 條件：這一列是 worker 的任務（engine 還沒寫入，或不是後端自己產生的）。"""
    return or_(col(Generation.engine).is_(None), col(Generation.engine).not_in(LOCAL_ENGINES))


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
    """產生 Generation.request_json；character_image 為 Storage 的物件 key（來源紀錄，不存 bytes），variation 一律寫入（可為 null）。
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


CHARACTER_MISSING = "角色參考圖檔案遺失，請重新選擇角色"


def decode_request(text: str, storage: Storage) -> GenerationRequest:
    """還原 GenerationRequest；character_image 的 key 從 Storage 讀成 bytes。
    key 不合法丟 ValueError；物件不存在丟 StorageNotFound。
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
        character_image=None if rel is None else storage.get(rel),
        variation=d.get("variation"),
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
        self.requeue_page_tasks()
        self.cancel_retired()
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
        """上次執行到一半的 GPU 任務放回佇列；寫到一半的上傳／合成（沒有圖）標成失敗。回傳放回佇列的數量。"""
        running = col(Generation.status) == GenStatus.RUNNING
        with self.sessions() as s:
            s.exec(
                update(Generation)
                .where(running, ~worker_job())
                .values(status=GenStatus.FAILED, error=LOCAL_INTERRUPTED, finished_at=utcnow())
            )
            res = s.exec(
                update(Generation)
                .where(running, worker_job())
                .values(status=GenStatus.QUEUED, started_at=None)
            )
            s.commit()
            return res.rowcount

    def requeue_page_tasks(self) -> int:
        """上次執行到一半的頁面流程放回佇列（確定性、便宜，重跑就好；不像生成標成失敗）。回傳數量。"""
        with self.sessions() as s:
            return pipeline.requeue_page_tasks(s)

    def cancel_retired(self) -> int:
        """把 QUEUED 且需要已停用路線（舊整頁、SDXL 快速動作／細緻背景）的列標為 CANCELLED（退回當天額度）。"""
        n = 0
        cancelled: list[int] = []
        with self.sessions() as s:
            rows = s.exec(
                select(Generation).where(col(Generation.status) == GenStatus.QUEUED)
            ).all()
            for gen in rows:
                try:
                    retired = _is_retired(json.loads(gen.request_json))
                except (ValueError, TypeError, AttributeError):
                    continue  # 無法解析的列留給 run_once 回報
                if retired:
                    gen.status = GenStatus.CANCELLED
                    gen.error = RETIRED
                    gen.finished_at = utcnow()
                    s.add(gen)
                    cancelled.append(gen.id)
                    n += 1
            s.flush()
            refund_generations(s, cancelled)
            s.commit()
        return n

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
            running = s.exec(
                select(Generation.id).where(col(Generation.status) == GenStatus.RUNNING, worker_job())
            ).first()
            return order.index(gen_id) + (1 if running is not None else 0)

    def _plan(self, s: Session) -> list[int]:
        """目前 QUEUED 任務的執行順序（與 _claim 取任務的順序相同）：建立時間，再 id。"""
        return list(
            s.exec(
                select(Generation.id)
                .where(col(Generation.status) == GenStatus.QUEUED)
                .order_by(col(Generation.created_at), col(Generation.id))
            ).all()
        )

    def _claim(self) -> Generation | None:
        """取最舊的 QUEUED 任務，以單一 UPDATE ... WHERE status=QUEUED 設為 RUNNING；
        rowcount 為 0 表示被搶走（或被取消），重試。"""
        while True:
            with self.sessions() as s:
                order = self._plan(s)
                if not order:
                    return None
                gen_id = order[0]
                res = s.exec(
                    update(Generation)
                    .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.QUEUED)
                    .values(status=GenStatus.RUNNING, started_at=utcnow())
                )
                s.commit()
                if res.rowcount == 1:
                    return s.get(Generation, gen_id)

    def _finish(self, gen_id: int, **values) -> bool:
        """只更新仍為 RUNNING 的列（執行中被改成 CANCELLED 等的不覆蓋），回傳有沒有改到。
        改成 FAILED 時在同一個交易退回當天額度（只有真的改到這一列才退）。"""
        with self.sessions() as s:
            res = s.exec(
                update(Generation)
                .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.RUNNING)
                .values(**values)
            )
            if res.rowcount == 1 and values.get("status") == GenStatus.FAILED:
                refund_generations(s, [gen_id])
            s.commit()
            return res.rowcount == 1

    # ---- 執行 ----
    async def run_once(self) -> bool:
        gen = self._claim()
        if gen is None:
            return False
        gen_id = gen.id
        self.state = WorkerState.RUNNING
        try:
            try:
                # Storage 可能是遠端（R2）：讀寫放到 thread，不卡住 event loop
                req = await asyncio.to_thread(decode_request, gen.request_json, self.storage)
            except StorageNotFound:
                self._fail(gen_id, CHARACTER_MISSING)
                return True
            except ValueError as e:
                if str(e) == RETIRED:  # 已停用的路線：只回報原因
                    self._fail(gen_id, RETIRED)
                    return True
                self._fail(gen_id, f"無法解析請求：{type(e).__name__}: {e}")
                return True
            except Exception as e:  # noqa: BLE001
                self._fail(gen_id, f"無法解析請求：{type(e).__name__}: {e}")
                return True
            result = await self.engine.generate(
                req, lambda value, maximum: self.progress.__setitem__(gen_id, (value, maximum))
            )
            rel = self.storage.generation_image(gen.book_id, gen_id)
            await asyncio.to_thread(self.storage.put, rel, result.image)
            mask_rel = None
            if result.extra_images.get("mask"):
                mask_rel = self.storage.generation_mask(gen.book_id, gen_id)
                mask_png = result.extra_images["mask"]
                if req.kind == GenKind.POSE:
                    mask_png = largest_component(mask_png)  # 丟掉與角色分離的陰影
                await asyncio.to_thread(self.storage.put, mask_rel, mask_png)
            done = self._finish(
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
            if done:
                self._after_success(gen_id)
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
        if self._finish(gen_id, status=GenStatus.FAILED, error=message, finished_at=utcnow()):
            try:
                with self.sessions() as s:
                    pipeline.on_generation_failed(s, gen_id)
            except Exception:  # noqa: BLE001  只記 log：生成已標成失敗
                log.exception("記下頁面需要注意失敗（generation %s）", gen_id)
        self.last_error = message
        self.state = WorkerState.IDLE

    def _after_success(self, gen_id: int) -> None:
        """頁面的背景／角色動作：自動選用並排頁面流程；任何失敗只記 log（生成已經成功）。"""
        try:
            with self.sessions() as s:
                pipeline.on_generation_succeeded(s, gen_id)
        except Exception:  # noqa: BLE001
            log.exception("自動選用／排頁面流程失敗（generation %s）", gen_id)

    # ---- 頁面流程（CPU 工作，排在生成之間）----
    async def run_page_tasks(self) -> int:
        """把排隊的頁面流程一個個做完（包括執行中新排進來的），回傳做了幾個。
        流程在 thread 裡跑（線稿是 CPU 工作，不卡 event loop）；工作的結束狀態也在 thread 裡寫，
        所以 stop() 取消等待時，跑到一半的流程仍會正常收尾。"""
        n = 0
        while True:
            with self.sessions() as s:
                task = pipeline.claim_page_task(s)
                if task is None:
                    return n
                task_id, page_id = task.id, task.page_id
            await asyncio.to_thread(self._run_page_task, task_id, page_id)
            n += 1

    def _run_page_task(self, task_id: int, page_id: int) -> None:
        status, error = PageTaskStatus.SUCCEEDED, None
        with self.sessions() as s:
            try:
                pipeline.run_page_pipeline(s, self.storage, page_id)
            except pipeline.PipelineFailed as e:
                status, error = PageTaskStatus.FAILED, e.error
            except Exception as e:  # noqa: BLE001  run_page_pipeline 自己會轉成 PipelineFailed；保險
                log.exception("page task %s crashed", task_id)
                status, error = PageTaskStatus.FAILED, f"{type(e).__name__}: {e}"
        for attempt in range(2):  # 收尾失敗（例如資料庫暫時鎖住）再試一次；仍失敗就留 RUNNING，重啟時重排
            try:
                with self.sessions() as s:
                    pipeline.finish_page_task(s, task_id, status, error)
                return
            except Exception:  # noqa: BLE001
                log.exception("page task %s：無法記下結果（第 %d 次）", task_id, attempt + 1)

    async def _loop(self) -> None:
        while True:
            # 先清除再取任務：任務執行中到達的 notify() 會讓事件保持 set，不會遺失
            self._wakeup.clear()
            try:
                did = await self.run_once()
                await self.run_page_tasks()  # 每張生成之後（與閒置時）先做完排隊的頁面流程
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
