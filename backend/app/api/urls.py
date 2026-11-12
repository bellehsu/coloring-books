"""API 網址的組法（唯一來源）：回應 model、總覽與分享頁都從這裡取網址，改格式時不會漏掉比對網址的地方。"""

from __future__ import annotations


def lineart_url(page_id: int, key: str | None) -> str:
    return f"/api/pages/{page_id}/lineart" + (f"?v={key}" if key else "")


def lineart_url_is_current(page_id: int, key: str | None, url: str | None) -> bool:
    """url（PageOut／overview 給的線稿網址）是不是頁面存的線稿 key（key）對應的那一個：
    也就是頁面上存的線稿（和 QA 結果）還是最新的。"""
    return bool(key) and url == lineart_url(page_id, key)


def generation_image_url(gen_id: int) -> str:
    return f"/api/generations/{gen_id}/image"


def generation_cutout_url(gen_id: int) -> str:
    return f"/api/generations/{gen_id}/cutout"


def shared_page_image_url(page_id: int, version: str) -> str:
    return f"/api/shared/pages/{page_id}/image.png?v={version}"
