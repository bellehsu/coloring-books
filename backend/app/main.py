"""FastAPI 進入點。Phase 1 只提供系統檢查端點。

啟動：uvicorn app.main:app --app-dir backend
啟動時會跑一次 preflight 並寫入 log；失敗只記錄，不讓服務當機。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings, load_settings
from .services.comfy_client import ComfyClient
from .services.preflight import format_report, run_preflight
from .services.workflow_registry import WorkflowRegistry

log = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or load_settings()
        app.state.registry = WorkflowRegistry(app.state.settings.paths.workflows_dir).load()
        report = await _preflight(app)
        (log.info if report.ok else log.warning)("preflight\n%s", format_report(report))
        yield

    app = FastAPI(title="Coloring Books", lifespan=lifespan)

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
