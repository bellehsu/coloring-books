"""LocalStorage 的簽章檔案端點，與圖片端點共用的「轉址到簽章網址」輔助。

GET /api/files/{key} 一律存在，但只驗證 LocalStorage 的 HMAC 簽章：其他後端（R2）的簽章網址
直接指向物件儲存，不經過這裡，所以這條路由對它們回 404。
"""

import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.api.deps import get_storage
from app.storage import LocalStorage, Storage, StorageNotFound

router = APIRouter(prefix="/api", tags=["files"])

REDIRECT_MAX_AGE = 60  # config.py 的 storage.signed_url_ttl_s 下限 = 這個值 + 30
REDIRECT_CACHE_CONTROL = f"private, max-age={REDIRECT_MAX_AGE}"


def signed_redirect(request: Request, storage: Storage, key: str) -> RedirectResponse:
    """302 到 storage.signed_url(key)。呼叫端要先確認物件存在（不存在要回 404，不是死網址）。"""
    ttl = request.app.state.settings.storage.signed_url_ttl_s
    return RedirectResponse(storage.signed_url(key, ttl), status_code=302,
                            headers={"Cache-Control": REDIRECT_CACHE_CONTROL})


@router.get("/files/{key:path}")
def get_file(
    key: str,
    exp: str = "",
    sig: str = "",
    storage: Storage = Depends(get_storage),
) -> Response:
    if not isinstance(storage, LocalStorage):
        raise HTTPException(404, "找不到檔案")
    if not storage.verify(key, exp, sig):  # 含 key 不合法（.. 等）、過期、簽章不符
        raise HTTPException(403, "網址無效或已過期")
    try:
        data = storage.get(key)
    except (StorageNotFound, ValueError):
        raise HTTPException(404, "圖片檔案遺失") from None
    media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(content=data, media_type=media_type,
                    headers={"Cache-Control": REDIRECT_CACHE_CONTROL})
