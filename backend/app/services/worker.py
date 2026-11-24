"""單一背景生成 worker：依建立時間（最舊的先）逐筆執行 QUEUED 的 Generation（單 GPU，絕不並行）。

每做完一張生成（以及閒置時），先把排隊的頁面流程（page_task，services/pipeline.py：CPU 工作）做完，
再做下一張生成。頁面的背景／角色動作生成成功時自動選用並排這頁的流程；失敗時頁面 attention="failed"。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import or_
from sqlmodel import Session, col, select, update

from app.models import Generation, GenKind, GenStatus, PageTaskStatus, utcnow
from app.services.engine import (
    RETIRED,
    EngineError,
    EngineUnavailable,
    GenerationEngine,
)
from app.services import auth, pipeline
from app.services.gen_request import (
    ENGINE_REQUEUES,
    ENGINE_UNAVAILABLE_SINCE,
    decode_request,
    is_retired,
)
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


MAX_ENGINE_REQUEUES = 3
# 連不上超過 MAX_ENGINE_REQUEUES 次、而且距第一次連不上已滿這麼多秒，才標成失敗（ComfyUI 重啟等短暫中斷不算）
ENGINE_GIVE_UP_AFTER_S = 600
WRITE_RETRY_DELAYS = (0.2, 0.5, 1.0)  # 收尾寫入失敗時的退避秒數
ENGINE_GAVE_UP = "繪圖服務一直連不上，已停止重試"


def _now() -> datetime:
    """目前時間（UTC）；測試替換它來模擬時間經過。"""
    return datetime.now(timezone.utc)


CHARACTER_MISSING = "角色參考圖檔案遺失，請重新選擇角色"
SOURCE_MISSING = "要清理的背景圖檔案遺失，請重新生成背景"


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
        self._in_engine = False  # 引擎正在跑一筆（stop() 時才需要中斷 ComfyUI）
        self._unfinished: dict[int, Callable[[], object]] = {}  # 收尾寫入一直失敗的列：下一輪迴圈前補寫
        self._unfinished_page_tasks: dict[int, tuple[PageTaskStatus, str | None]] = {}  # 頁面流程的結果同上

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
        pipeline.set_wakeup(self.notify)  # 不經 API 排的頁面流程（例如線稿的 key 變了）也叫得醒

    async def stop(self) -> None:
        task, self._task = self._task, None
        if pipeline._wakeup == self.notify:
            pipeline.set_wakeup(None)
        if task is not None:
            was_in_engine = self._in_engine
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if was_in_engine:
                # 先取消任務再中斷：否則 ComfyUI 的「執行被中斷」會讓還活著的任務把這筆標成失敗，
                # 而不是留在 RUNNING 讓重啟時放回佇列。盡力而為，失敗只記 log
                try:
                    await self.engine.interrupt()
                except Exception:  # noqa: BLE001
                    log.warning("stop：中斷引擎失敗", exc_info=True)
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
            # 執行到一半被中斷（不是連不上）：連不上的紀錄重新算
            for gid, rj in s.exec(select(Generation.id, Generation.request_json).where(running, worker_job())).all():
                cleared = self._without_outage(rj)
                if cleared != rj:
                    s.exec(update(Generation).where(col(Generation.id) == gid, running).values(request_json=cleared))
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
                    retired = is_retired(json.loads(gen.request_json))
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
        return self.queue_positions([gen_id])[gen_id]

    def queue_positions(self, ids: list[int]) -> dict[int, int | None]:
        """批次版：一個 session 算出目前的執行順序與 RUNNING，再對每筆查表（A15）。
        RUNNING＝0；QUEUED＝在 QUEUED 中的順序（有 RUNNING 時 +1）；其餘（含不存在）＝None。"""
        if not ids:
            return {}
        with self.sessions() as s:
            status = dict(
                s.exec(select(Generation.id, Generation.status).where(col(Generation.id).in_(set(ids)))).all()
            )
            order: dict[int, int] = {}
            offset = 0
            if any(st == GenStatus.QUEUED for st in status.values()):
                order = {gid: i for i, gid in enumerate(self._plan(s))}
                running = s.exec(
                    select(Generation.id).where(col(Generation.status) == GenStatus.RUNNING, worker_job())
                ).first()
                offset = 1 if running is not None else 0
        out: dict[int, int | None] = {}
        for gid in ids:
            st = status.get(gid)
            if st == GenStatus.RUNNING:
                out[gid] = 0
            elif st == GenStatus.QUEUED and gid in order:  # 不在順序裡＝讀取之間狀態剛好改變
                out[gid] = order[gid] + offset
            else:
                out[gid] = None
        return out

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
                auth.cancel_revoked_queues(s)  # 撤銷者的排隊工作先取消，不能被認領
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

    def _source_key(self, gen: Generation) -> str | None:
        """背景清理：來源 generation 的圖檔 key；沒有來源回 None，來源沒有圖就當檔案遺失。"""
        if gen.source_generation_id is None:
            return None
        with self.sessions() as s:
            src = s.get(Generation, gen.source_generation_id)
            if src is None or not src.image_path:
                raise StorageNotFound(f"source generation {gen.source_generation_id}")
            return src.image_path

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
        gen = await asyncio.to_thread(self._claim)  # 同步 DB 工作放到 thread，不卡 event loop（A17）
        if gen is None:
            return False
        gen_id = gen.id
        self.state = WorkerState.RUNNING
        try:
            try:
                # Storage 可能是遠端（R2）：讀寫放到 thread，不卡住 event loop
                source_key = await asyncio.to_thread(self._source_key, gen)
                req = await asyncio.to_thread(decode_request, gen.request_json, self.storage, source_key)
            except StorageNotFound:
                await self._fail_retrying(
                    gen_id, SOURCE_MISSING if gen.source_generation_id is not None else CHARACTER_MISSING)
                return True
            except ValueError as e:
                if str(e) == RETIRED:  # 已停用的路線：只回報原因
                    await self._fail_retrying(gen_id, RETIRED)
                    return True
                await self._fail_retrying(gen_id, f"無法解析請求：{type(e).__name__}: {e}")
                return True
            except Exception as e:  # noqa: BLE001
                await self._fail_retrying(gen_id, f"無法解析請求：{type(e).__name__}: {e}")
                return True
            self._in_engine = True
            try:
                result = await self.engine.generate(
                    req, lambda value, maximum: self.progress.__setitem__(gen_id, (value, maximum))
                )
            finally:
                self._in_engine = False
            mask_png = result.extra_images.get("mask")
            if mask_png and req.kind == GenKind.POSE:
                try:  # 寫任何物件之前先檢查：失敗時不留下沒有列指到的圖
                    mask_png = await asyncio.to_thread(largest_component, mask_png)  # 丟掉與角色分離的陰影
                except ValueError as e:  # 去背結果太細長（compose.DEGENERATE_MASK）
                    await self._fail_retrying(gen_id, str(e))
                    return True
            rel = self.storage.generation_image(gen.book_id, gen_id)
            await asyncio.to_thread(self.storage.put, rel, result.image)
            mask_rel = None
            if mask_png:
                mask_rel = self.storage.generation_mask(gen.book_id, gen_id)
                await asyncio.to_thread(self.storage.put, mask_rel, mask_png)
            values = dict(
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
            cleared = self._without_outage(gen.request_json)
            if cleared != gen.request_json:  # 引擎回應成功：連不上的紀錄清掉
                values["request_json"] = cleared

            def write_success() -> None:
                if self._finish(gen_id, **values):
                    self._after_success(gen_id)
                else:
                    self._drop_if_gone(gen_id, [rel, mask_rel])

            await self._write_retrying(gen_id, write_success)
            self.last_error = None
            self.state = WorkerState.IDLE
        except EngineUnavailable as e:
            count, since = self._outage(gen.request_json)
            requeues, now = count + 1, _now()
            since = since or now
            if requeues > MAX_ENGINE_REQUEUES and (now - since).total_seconds() >= ENGINE_GIVE_UP_AFTER_S:
                await self._fail_retrying(gen_id, ENGINE_GAVE_UP)
            else:
                requeue_json = self._with_outage(gen.request_json, requeues, since)
                await self._write_retrying(
                    gen_id,
                    lambda: self._finish(gen_id, status=GenStatus.QUEUED, started_at=None, request_json=requeue_json),
                )
                self.last_error = str(e)
                self.state = WorkerState.ENGINE_UNAVAILABLE
        except EngineError as e:
            await self._fail_retrying(gen_id, str(e))
        except Exception as e:  # noqa: BLE001  單筆任務的任何例外都不可殺死 worker
            log.exception("generation %s crashed", gen_id)
            await self._fail_retrying(gen_id, f"非預期錯誤：{type(e).__name__}: {e}")
        finally:
            self.progress.pop(gen_id, None)
        return True

    @staticmethod
    def _request_dict(request_json: str | None) -> dict:
        try:
            d = json.loads(request_json or "{}")
        except (TypeError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    @classmethod
    def _outage(cls, request_json: str | None) -> tuple[int, datetime | None]:
        """（因連不上而放回佇列的次數, 第一次連不上的時間）；沒有或格式不對分別是 0、None。"""
        d = cls._request_dict(request_json)
        n = d.get(ENGINE_REQUEUES, 0)
        if not isinstance(n, int) or isinstance(n, bool):
            n = 0
        try:
            since = datetime.fromisoformat(d[ENGINE_UNAVAILABLE_SINCE])
        except (KeyError, TypeError, ValueError):
            since = None
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        return n, since

    @classmethod
    def _with_outage(cls, request_json: str | None, n: int, since: datetime) -> str:
        d = cls._request_dict(request_json)
        d[ENGINE_REQUEUES] = n
        d[ENGINE_UNAVAILABLE_SINCE] = since.isoformat()
        return json.dumps(d, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _without_outage(cls, request_json: str) -> str:
        """拿掉連不上的紀錄；本來就沒有（或無法解析）時原樣回傳。"""
        d = cls._request_dict(request_json)
        if ENGINE_REQUEUES not in d and ENGINE_UNAVAILABLE_SINCE not in d:
            return request_json
        d.pop(ENGINE_REQUEUES, None)
        d.pop(ENGINE_UNAVAILABLE_SINCE, None)
        return json.dumps(d, ensure_ascii=False, sort_keys=True)

    async def _write_retrying(self, gen_id: int, write: Callable[[], object]) -> None:
        """收尾寫入（資料庫暫時鎖住等）失敗時退避重試；仍失敗就記在 _unfinished，下一輪迴圈前補寫。從不丟例外。"""
        for delay in (None, *WRITE_RETRY_DELAYS):
            if delay is not None:
                await asyncio.sleep(delay)
            try:
                await asyncio.to_thread(write)
                self._unfinished.pop(gen_id, None)
                return
            except Exception:  # noqa: BLE001
                log.exception("generation %s：收尾寫入失敗", gen_id)
        self._unfinished[gen_id] = write

    async def _fail_retrying(self, gen_id: int, message: str) -> None:
        await self._write_retrying(gen_id, lambda: self._fail(gen_id, message))
        if gen_id not in self._unfinished:  # 狀態只在 event loop 這一側改：stop() 之後 thread 才收尾也不會蓋掉 STOPPED
            self.last_error = message
            self.state = WorkerState.IDLE

    async def _retry_unfinished(self) -> None:
        for gen_id, write in list(self._unfinished.items()):
            try:
                await asyncio.to_thread(write)
                self._unfinished.pop(gen_id, None)
            except Exception:  # noqa: BLE001
                log.exception("generation %s：補寫收尾仍失敗", gen_id)
        for task_id, (status, error) in list(self._unfinished_page_tasks.items()):
            try:
                await asyncio.to_thread(self._finish_page_task, task_id, status, error)
                self._unfinished_page_tasks.pop(task_id, None)
            except Exception:  # noqa: BLE001
                log.exception("page task %s：補寫結果仍失敗", task_id)

    def _fail(self, gen_id: int, message: str) -> None:
        if self._finish(gen_id, status=GenStatus.FAILED, error=message, finished_at=utcnow()):
            try:
                with self.sessions() as s:
                    pipeline.on_generation_failed(s, gen_id)
            except Exception:  # noqa: BLE001  只記 log：生成已標成失敗
                log.exception("記下頁面需要注意失敗（generation %s）", gen_id)

    def _drop_if_gone(self, gen_id: int, keys: list[str | None]) -> None:
        """生成途中整本書被刪了（列不見）：刪掉剛寫的物件，免得留下沒有列指到的孤兒。盡力而為。"""
        try:
            with self.sessions() as s:
                gone = s.get(Generation, gen_id) is None
            if gone:
                for key in keys:
                    if key:
                        self.storage.delete(key)
        except Exception:  # noqa: BLE001
            log.warning("generation %s：書已刪除，清掉剛寫的物件失敗（storage_gc 會清）", gen_id, exc_info=True)

    def _after_success(self, gen_id: int) -> None:
        """頁面的背景／角色動作：自動選用並排頁面流程（生成已經成功，這裡不丟例外）。
        失敗時至少把頁面排進流程（流程第一步會補選圖），頁面才不會一直停在「製作中」。"""
        try:
            with self.sessions() as s:
                pipeline.on_generation_succeeded(s, gen_id)
            return
        except Exception:  # noqa: BLE001
            log.exception("自動選用／排頁面流程失敗（generation %s），改為只排頁面流程", gen_id)
        try:
            with self.sessions() as s:
                pipeline.enqueue_after_generation(s, gen_id)
        except Exception:  # noqa: BLE001
            log.exception("排頁面流程也失敗（generation %s）：要等「繼續做」或重啟", gen_id)

    # ---- 頁面流程（CPU 工作，排在生成之間）----
    async def run_page_tasks(self) -> int:
        """把排隊的頁面流程一個個做完（包括執行中新排進來的），回傳做了幾個。
        流程在 thread 裡跑（線稿是 CPU 工作，不卡 event loop）；工作的結束狀態也在 thread 裡寫，
        所以 stop() 取消等待時，跑到一半的流程仍會正常收尾。"""
        n = 0
        while True:
            claimed = await asyncio.to_thread(self._claim_page_task)  # 資料庫讀寫不卡 event loop
            if claimed is None:
                return n
            await asyncio.to_thread(self._run_page_task, *claimed)
            n += 1

    def _claim_page_task(self) -> tuple[int, int] | None:
        with self.sessions() as s:
            auth.cancel_revoked_queues(s)
            task = pipeline.claim_page_task(s)
            return None if task is None else (task.id, task.page_id)

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
        for attempt in range(2):  # 收尾失敗（例如資料庫暫時鎖住）再試一次；仍失敗就記下來，下一輪迴圈補寫
            try:
                self._finish_page_task(task_id, status, error)
                return
            except Exception:  # noqa: BLE001
                log.exception("page task %s：無法記下結果（第 %d 次）", task_id, attempt + 1)
        self._unfinished_page_tasks[task_id] = (status, error)

    def _finish_page_task(self, task_id: int, status: PageTaskStatus, error: str | None) -> None:
        with self.sessions() as s:
            pipeline.finish_page_task(s, task_id, status, error)

    async def _loop(self) -> None:
        while True:
            # 先清除再取任務：任務執行中到達的 notify() 會讓事件保持 set，不會遺失
            self._wakeup.clear()
            try:
                await self._retry_unfinished()
                did = await self.run_once()
                await self.run_page_tasks()  # 每張生成之後（與閒置時）先做完排隊的頁面流程
            except Exception:  # noqa: BLE001  例如資料庫暫時錯誤；退避後繼續
                log.exception("worker loop error")
                self.state = WorkerState.IDLE
                await self._wait(self.retry_backoff_s)
                continue
            if not did:
                self.state = WorkerState.IDLE
                self.last_error = None
                if self._unfinished or self._unfinished_page_tasks:
                    # 還有沒寫成的收尾：退避後再試（不等別的事件叫醒，否則頁面會一直停在「製作中」）
                    await self._wait(self.retry_backoff_s)
                else:
                    # 佇列已空、沒有待補寫的收尾：無限期等待 notify()
                    await self._wakeup.wait()
            elif self.state == WorkerState.ENGINE_UNAVAILABLE:
                await self._wait(self.retry_backoff_s)

    async def _wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout)
        except asyncio.TimeoutError:
            pass
