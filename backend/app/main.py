"""FastAPI 進入點：組裝 DB、worker、書／頁／生成 API 與系統檢查端點。

啟動：uvicorn app.main:app --app-dir backend
啟動時會跑一次 preflight 並寫入 log；失敗只記錄，不讓服務當機（startup_preflight=False 時略過，例如假後端）。
preflight 結果快取在 app.state.preflight（啟動時與每次 GET /api/system/preflight 更新），
角色動作的模式可用性（/api/system/pose-modes、POST /poses 的 503）只看這份快取。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import books, compose, export, generations, lineart, pages, review
from .api.schemas import PoseModesOut
from .config import Settings, load_settings
from .db import init_db, make_engine, session_factory
from .services.comfy_client import ComfyClient
from .services.comfy_engine import ComfyEngine
from .services.engine import GenerationEngine
from .services.pose_modes import availability
from .services.preflight import format_report, run_preflight
from .services.worker import GenerationWorker
from .services.workflow_registry import WorkflowRegistry
from .storage import Storage

log = logging.getLogger("app")


def create_app(
    settings: Settings | None = None,
    *,
    engine: GenerationEngine | None = None,
    start_worker: bool = True,
    startup_preflight: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings or load_settings()
        app.state.settings = s
        app.state.registry = WorkflowRegistry(s.paths.workflows_dir).load()
        db = make_engine(s.paths.database)
        init_db(db)
        app.state.db = db
        app.state.sessions = session_factory(db)
        app.state.storage = Storage(s.paths.storage_dir)
        gen_engine = engine or ComfyEngine(
            client_factory=lambda: ComfyClient(
                s.comfy.url, request_timeout_s=s.comfy.request_timeout_s
            ),
            registry=app.state.registry,
            run_timeout_s=s.comfy.run_timeout_s,
        )
        app.state.engine = gen_engine
        app.state.worker = GenerationWorker(
            gen_engine, app.state.sessions, app.state.storage, s.queue.retry_backoff_s
        )
        app.state.preflight = None  # None＝狀態未知（兩種角色動作模式都算可用）
        try:
            if start_worker:
                await app.state.worker.start()
            if startup_preflight:
                try:
                    report = await _preflight(app)
                except Exception:  # noqa: BLE001  preflight 失敗不可讓服務當機
                    log.exception("preflight failed")
                else:
                    (log.info if report.ok else log.warning)("preflight\n%s", format_report(report))
            yield
        finally:
            await app.state.worker.stop()
            db.dispose()

    app = FastAPI(title="Coloring Books", lifespan=lifespan)
    app.include_router(books.router)
    app.include_router(pages.router)
    app.include_router(compose.router)
    app.include_router(generations.router)
    app.include_router(lineart.router)
    app.include_router(review.router)
    app.include_router(export.router)

    async def _preflight(app: FastAPI):
        """跑 preflight 並更新快取 app.state.preflight。"""
        s: Settings = app.state.settings
        async with ComfyClient(s.comfy.url, request_timeout_s=s.comfy.request_timeout_s) as client:
            report = await run_preflight(client, app.state.registry)
        app.state.preflight = report
        return report

    @app.get("/api/system/preflight")
    async def preflight(reload: bool = True) -> dict:
        if reload:
            app.state.registry = WorkflowRegistry(app.state.settings.paths.workflows_dir).load()
            # 引擎也要改用新登錄，否則 preflight 與實際生成會看到不同的 workflow／hash
            if isinstance(app.state.engine, ComfyEngine):
                app.state.engine.registry = app.state.registry
        return (await _preflight(app)).to_dict()

    @app.get("/api/system/pose-modes", response_model=PoseModesOut)
    def pose_modes() -> dict:
        """角色動作精準／快速模式能不能用：只讀快取的 preflight，不連 ComfyUI。"""
        return availability(app.state.preflight)

    return app


app = create_app()
