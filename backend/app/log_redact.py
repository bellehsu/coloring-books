"""Log 去識別：邀請連結的 token 不可寫進 log（spec 反目標：憑證不進 repo、log、錯誤訊息）。

uvicorn 的 access log 會印出完整路徑 `"GET /i/<token> HTTP/1.1" 303`；拿到 log 的人就能用那個連結登入。
InviteTokenFilter 把 `/i/<任何東西>` 改成 `/i/***`（只改這一段，查詢字串與其他欄位不動）。

install_log_redaction() 裝在 uvicorn.access logger 上（logger 的 filter 不會被 logging.config.dictConfig 清掉，
uvicorn 先設定 logging 再 import app、或先建 app 再設定都一樣有效）。create_app 會呼叫它，
所以 dev.sh（`python -m uvicorn app.main:app`）、`uvicorn.run(app)` 與雲端入口都不用另外設定。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

MASKED = "/i/***"
_INVITE_PATH = re.compile(r"/i/[^\s\"'?#/]+")
ACCESS_LOGGERS = ("uvicorn.access",)


def redact(text: str) -> str:
    return _INVITE_PATH.sub(MASKED, text)


def _redact_arg(value):
    return redact(value) if isinstance(value, str) else value


class InviteTokenFilter(logging.Filter):
    """改寫紀錄的訊息與參數（參數個數與型別不變：uvicorn 的 AccessFormatter 會拆開它們）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact_arg(a) for a in args)
        elif isinstance(args, Mapping):
            record.args = {k: _redact_arg(v) for k, v in args.items()}
        return True


def install_log_redaction() -> None:
    """在 access log 的 logger 上裝 InviteTokenFilter（重複呼叫只裝一次）。"""
    for name in ACCESS_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, InviteTokenFilter) for f in logger.filters):
            logger.addFilter(InviteTokenFilter())
