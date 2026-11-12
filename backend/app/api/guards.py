"""請求防護（純 ASGI middleware，在路由與相依性之前執行）。

- BodyLimitMiddleware：body 大小上限（B1）。FastAPI 會在 current_owner 之前就讀完整個 body，
  所以要在更外層先擋；Content-Length 超過直接 413，否則邊收邊數（chunked 也涵蓋）。
- CrossSiteWriteMiddleware：瀏覽器標示為跨站（Sec-Fetch-Site: cross-site）的寫入一律 403（B2）。
  不比對 Origin 與 Host：Next.js rewrite 代理會改 Host。
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.uploads import MAX_UPLOAD_BYTES

UPLOAD_LIMIT = MAX_UPLOAD_BYTES + 1_000_000  # 檔案本體 + multipart 外框
API_LIMIT = 2_000_000
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _limit_for(path: str) -> int | None:
    if path.endswith("/upload"):
        return UPLOAD_LIMIT
    if path.startswith("/api/"):
        return API_LIMIT
    return None


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _limit_for(scope["path"])
        if limit is None:
            await self.app(scope, receive, send)
            return
        too_large = JSONResponse({"detail": "檔案太大"}, status_code=413)
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > limit:
                    await too_large(scope, receive, send)
                    return
                break

        received = 0
        exceeded = False
        started = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    # handler 拿不到被截斷的 body：當作客戶端中斷
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                return  # 截斷 body 造成的錯誤回應（例如 400）丟掉，改回 413
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, counting_receive, tracking_send)
        if exceeded and not started:
            await too_large(scope, receive, send)


class CrossSiteWriteMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in WRITE_METHODS:
            for name, value in scope["headers"]:
                if name == b"sec-fetch-site" and value == b"cross-site":
                    await JSONResponse(
                        {"detail": "不接受從其他網站送來的修改"}, status_code=403
                    )(scope, receive, send)
                    return
        await self.app(scope, receive, send)
