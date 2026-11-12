"""FastAPI 進入點：組裝 DB、worker、書／頁／生成 API 與系統檢查端點。

啟動：uvicorn app.main:app --app-dir backend
啟動時會跑一次 preflight 並寫入 log；失敗只記錄，不讓服務當機（startup_preflight=False 時略過，例如假後端）。
preflight 結果快取在 app.state.preflight（啟動時與每次 GET /api/system/preflight 更新）。
生成請求不看這份快取（不先擋）：缺模型或節點時由生成失敗回報。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from .api import (
    admin, books, compose, export, files, generations, history, invites, lineart, pages, produce, review, shared,
)
from .api.deps import require_admin, require_owned_path
from .config import Settings, load_settings
from .db import init_db, make_engine, session_factory
from .log_redact import install_log_redaction
from .services.captions import check_caption_font
from .services.comfy_client import ComfyClient
from .services.comfy_engine import ComfyEngine
from .services.engine import GenerationEngine
from .services.instance import ensure_instance, mismatch_hint, storage_label
from .services.preflight import format_report, run_preflight
from .services.worker import GenerationWorker
from .api.guards import BodyLimitMiddleware, CrossSiteWriteMiddleware
from .services.workflow_registry import WorkflowRegistry
from .storage import make_storage

log = logging.getLogger("app")


def create_app(
    settings: Settings | None = None,
    *,
    engine: GenerationEngine | None = None,
    start_worker: bool = True,
    startup_preflight: bool = True,
) -> FastAPI:
    # 邀請連結的 token 不進 access log（GET /i/<token> → /i/***）。uvicorn CLI（dev.sh）import 本模組時就會裝上
    install_log_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        install_log_redaction()  # 建 app 之後才重設 logging 的啟動方式也涵蓋（重複呼叫只裝一次）
        s = settings or load_settings()
        app.state.settings = s
        app.state.registry = WorkflowRegistry(s.paths.workflows_dir).load()
        db = make_engine(s.paths.database)
        init_db(db)
        app.state.db = db
        app.state.sessions = session_factory(db)
        app.state.storage = make_storage(s)
        _check_storage_instance(app)
        gen_engine = engine or ComfyEngine(
            client_factory=lambda: ComfyClient(
                s.comfy.url, request_timeout_s=s.comfy.request_timeout_s
            ),
            registry=app.state.registry,
            run_timeout_s=s.comfy.run_timeout_s,
        )
        app.state.engine = gen_engine
        app.state.worker = GenerationWorker(
            gen_engine,
            app.state.sessions,
            app.state.storage,
            s.queue.retry_backoff_s,
        )
        app.state.preflight = None  # None＝還沒檢查過
        font_problem = check_caption_font(s.paths.caption_font)
        if font_problem:  # 不擋啟動：只有「有頁面文字」的頁面匯出會 409
            log.warning("頁面文字字型不能用（paths.caption_font）：%s", font_problem)
        if s.auth.dev_owner:
            log.warning(
                "!!! auth.dev_owner=%r：沒有 cookie 的請求一律當作 admin「%s」（看得到、改得到所有人的繪本）。"
                "只限本機開發，對外部署不可設定（設成 null／移除 APP_AUTH__DEV_OWNER）!!!",
                s.auth.dev_owner,
                s.auth.dev_owner,
            )
        try:
            if start_worker:
                await app.state.worker.start()
            if startup_preflight:
                try:
                    report = await _preflight(app)
                except Exception:  # noqa: BLE001  preflight 失敗不可讓服務當機
                    log.exception("preflight failed")
                else:
                    font_ok = report.caption_font is None or report.caption_font.ok
                    (log.info if report.ok and font_ok else log.warning)("preflight\n%s", format_report(report))
            yield
        finally:
            await app.state.worker.stop()
            db.dispose()

    app = FastAPI(title="Coloring Books", lifespan=lifespan)
    # 在路由與相依性之前：先擋超大 body（B1）與跨站寫入（B2）
    app.add_middleware(CrossSiteWriteMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    # 每個 API 路由先確認登入（current_owner），再確認路徑裡的 book／page／generation id 屬於這個 owner
    # （別人的 → 404），都在 body 驗證與端點本身之前。例外：/api/files（HMAC 簽章就是憑證）與 /i/{token}。
    owned = [Depends(require_owned_path)]
    for r in (books, pages, compose, generations, lineart, review, export, produce, history):
        app.include_router(r.router, dependencies=owned)
    # 分享（唯讀）：不掛 require_owned_path（不是擁有者），但每個端點自己要 current_owner；見 api/shared.py
    app.include_router(shared.router)
    app.include_router(files.router)
    app.include_router(invites.router)
    # 管理者的邀請管理與 /api/me：沒有 book／page／generation id，自己的相依性做登入與管理者檢查（404）
    app.include_router(admin.me_router)
    app.include_router(admin.router)

    def _check_storage_instance(app: FastAPI) -> None:
        """DB 與 storage 的身分標記：新部署（或既有部署第一次升級）建立；對不上或不能自動建立時只記警告、
        照常服務（storage_gc 與 delete-data 會拒絕執行）。R2 連不上時受 boto3 逾時限制（見 storage.R2Storage）。"""
        try:
            with app.state.sessions() as session:
                res = ensure_instance(session, app.state.storage)
        except Exception:  # noqa: BLE001  檢查失敗（例如 storage 暫時連不上）不可讓服務當機
            log.warning("!!! storage instance 身分標記檢查失敗", exc_info=True)
            return
        if res.adoption_refused:
            log.warning(
                "!!! storage instance 身分標記：資料庫沒有任何書，但 storage（%s）的 books/ 已經有物件且沒有標記，"
                "不自動建立（避免空的新資料庫認領別人的 bucket）。storage_gc 與 invites.py delete-data 會拒絕執行。"
                "確認 paths.database 與 storage 設定是同一組後，執行 scripts/storage_gc.py --init-instance !!!",
                storage_label(app.state.storage),
            )
        elif not res.ok:
            log.warning(
                "!!! storage instance 身分標記對不上：%s。資料庫與 storage（資料夾／bucket）可能不是一組；"
                "storage_gc 與 invites.py delete-data 會拒絕執行。%s !!!",
                res.describe(),
                mismatch_hint(res),
            )
        elif res.created:
            log.info("storage instance 身分標記已建立：%s", res.db_id)

    async def _preflight(app: FastAPI):
        """跑 preflight 並更新快取 app.state.preflight。"""
        s: Settings = app.state.settings
        async with ComfyClient(s.comfy.url, request_timeout_s=s.comfy.request_timeout_s) as client:
            report = await run_preflight(client, app.state.registry, caption_font=s.paths.caption_font)
        app.state.preflight = report
        return report

    @app.get("/api/system/preflight", dependencies=[Depends(require_admin)])
    async def preflight(reload: bool = True) -> dict:
        if reload:
            app.state.registry = WorkflowRegistry(app.state.settings.paths.workflows_dir).load()
            # 引擎也要改用新登錄，否則 preflight 與實際生成會看到不同的 workflow／hash
            if isinstance(app.state.engine, ComfyEngine):
                app.state.engine.registry = app.state.registry
        return (await _preflight(app)).to_dict()

    return app


app = create_app()
