"""FastAPI 進入點。Phase 1 只提供系統檢查端點。

啟動：uvicorn app.main:app --app-dir backend
啟動時會跑一次 preflight 並寫入 log；失敗只記錄，不讓服務當機。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import books
from .config import Settings, load_settings
from .db import init_db, make_engine, session_factory
from .services.comfy_client import ComfyClient
from .services.comfy_engine import ComfyEngine
from .services.engine import GenerationEngine
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
        app.state.worker = GenerationWorker(
            gen_engine, app.state.sessions, app.state.storage, s.queue.retry_backoff_s
        )
        try:
            if start_worker:
                await app.state.worker.start()
            report = await _preflight(app)
            (log.info if report.ok else log.warning)("preflight\n%s", format_report(report))
            yield
        finally:
            await app.state.worker.stop()
            db.dispose()

    app = FastAPI(title="Coloring Books", lifespan=lifespan)
    app.include_router(books.router)

    async def _preflight(app: FastAPI):
        s: Settings = app.state.settings
        async with ComfyClient(s.comfy.url, request_timeout_s=s.comfy.request_timeout_s) as client:
            return await run_preflight(client, app.state.registry)

    @app.get("/api/system/preflight")
    async def preflight(reload: bool = True) -> dict:
        if reload:
            app.state.registry = WorkflowRegistry(app.state.settings.paths.workflows_dir).load()
        return (await _preflight(app)).to_dict()

    return app


app = create_app()
