"""共用相依：從 request.app.state 取得 session、storage、worker。"""

from collections.abc import Iterator

from fastapi import Request
from sqlmodel import Session

from app.services.worker import GenerationWorker
from app.storage import Storage


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_worker(request: Request) -> GenerationWorker:
    return request.app.state.worker
