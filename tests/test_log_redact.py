"""邀請連結的 token 不可進 log（final review B-I1；spec 反目標：憑證不進 repo、log、錯誤訊息）。

uvicorn 的 access log 會印出完整路徑 `GET /i/<token>`；app.log_redact 的 filter 把它改成 `/i/***`。
create_app 會裝上它，所以 dev.sh（`python -m uvicorn app.main:app`）、uvicorn.run(app) 與測試都有效。
"""

from __future__ import annotations

import logging
import logging.config
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml
from uvicorn.config import LOGGING_CONFIG

from app.config import load_settings
from app.log_redact import MASKED, InviteTokenFilter, install_log_redaction, redact
from app.main import create_app
from tests.fake_engine import FakeEngine

REPO = Path(__file__).resolve().parents[1]
TOKEN = "s3cr3t-T0ken_abcdefghijklmnop"


def access_record(path: str) -> logging.LogRecord:
    """與 uvicorn h11/httptools 的 access log 相同形狀的紀錄。"""
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", path, "1.1", 303), None,
    )


@pytest.mark.parametrize("path, want", [
    (f"/i/{TOKEN}", "/i/***"),
    (f"/i/{TOKEN}?utm=x", "/i/***?utm=x"),
    (f"/i/{TOKEN}/", "/i/***/"),
    ("/api/books", "/api/books"),
    ("/invalid-link", "/invalid-link"),
    ("/i/", "/i/"),
])
def test_redact(path, want):
    assert redact(path) == want


def test_filter_masks_access_record_and_keeps_shape():
    rec = access_record(f"/i/{TOKEN}")
    assert InviteTokenFilter().filter(rec) is True
    assert len(rec.args) == 5  # uvicorn 的 AccessFormatter 會拆這 5 個值
    assert rec.getMessage() == f'127.0.0.1:5000 - "GET {MASKED} HTTP/1.1" 303'
    assert TOKEN not in rec.getMessage()


def test_filter_masks_message_text_and_mapping_args():
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, f"opened http://h/i/{TOKEN} ok", None, None)
    InviteTokenFilter().filter(rec)
    assert TOKEN not in rec.getMessage()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "%(p)s", ({"p": f"/i/{TOKEN}"},), None)
    InviteTokenFilter().filter(rec)
    assert rec.getMessage() == MASKED


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


def _emit_access(path: str) -> list[str]:
    logger = logging.getLogger("uvicorn.access")
    cap = _Capture()
    cap.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(cap)
    try:
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 303)
    finally:
        logger.removeHandler(cap)
    return cap.lines


@pytest.fixture
def clean_access_logger():
    logger = logging.getLogger("uvicorn.access")
    saved = (list(logger.filters), list(logger.handlers), logger.level, logger.propagate)
    for f in list(logger.filters):
        logger.removeFilter(f)
    yield logger
    logger.filters[:] = saved[0]
    logger.handlers[:] = saved[1]
    logger.setLevel(saved[2])
    logger.propagate = saved[3]


def test_install_is_idempotent(clean_access_logger):
    install_log_redaction()
    install_log_redaction()
    assert sum(isinstance(f, InviteTokenFilter) for f in clean_access_logger.filters) == 1


@pytest.mark.parametrize("order", ["uvicorn_cli", "uvicorn_run_app_object"])
def test_create_app_installs_filter_regardless_of_uvicorn_logging_setup(clean_access_logger, settings_file, order):
    """uvicorn CLI 先設定 logging 再 import app；uvicorn.run(app) 先建 app 再設定 logging。兩種都要遮。"""
    if order == "uvicorn_cli":
        logging.config.dictConfig(LOGGING_CONFIG)
        create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    else:
        create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
        logging.config.dictConfig(LOGGING_CONFIG)
    lines = _emit_access(f"/i/{TOKEN}")
    assert lines == [f'127.0.0.1:5000 - "GET {MASKED} HTTP/1.1" 303']


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uvicorn_cli_access_log_has_no_token(tmp_path, workflows_dir):
    """與 dev.sh 相同的啟動方式（python -m uvicorn app.main:app --app-dir backend），實際開一次邀請連結。"""
    storage = tmp_path / "storage"
    storage.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump({
        # 沒有東西在聽的本機埠：worker／preflight 連不上 ComfyUI 只記 log，不碰網路
        "comfy": {"url": f"http://127.0.0.1:{_free_port()}", "models_root": str(models)},
        "paths": {"workflows_dir": str(workflows_dir), "storage_dir": str(storage),
                  "database": str(storage / "app.db")},
    }), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("APP_") and k != "PYTHONPATH"}
    env["APP_SETTINGS_FILE"] = str(settings)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        status = None
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                status = httpx.get(f"http://127.0.0.1:{port}/i/{TOKEN}", follow_redirects=False).status_code
                break
            except httpx.TransportError:
                time.sleep(0.2)
        assert status == 303
        time.sleep(0.3)  # access log 在回應後寫出
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=15)
    assert '"GET /i/*** HTTP/1.1" 303' in out, out
    assert TOKEN not in out
