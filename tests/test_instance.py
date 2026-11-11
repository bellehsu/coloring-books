"""DB ↔ storage 的身分標記（storage_instance_id）：GC 只在兩邊一致時執行（Phase 10 Task 7 修正第 3 輪）。"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.services import instance
from tests.fake_engine import FakeEngine


def _app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


def _ids(app):
    with app.state.sessions() as s:
        return instance.db_instance_id(s), instance.bucket_instance_id(app.state.storage)


def test_fresh_deployment_creates_matching_markers(settings_file):
    app = _app(settings_file)
    with TestClient(app):
        db_id, bucket_id = _ids(app)
    assert db_id and db_id == bucket_id and len(db_id) >= 32
    raw = json.loads(app.state.storage.get(instance.MARKER_KEY))
    assert raw == {"storage_instance_id": db_id}
    app2 = _app(settings_file)
    with TestClient(app2):
        assert _ids(app2) == (db_id, db_id)  # 重啟不換


def _caplog_warn(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and "instance" in r.getMessage()]


@pytest.mark.parametrize("case", ["bucket_missing", "db_missing", "differ"])
def test_startup_mismatch_warns_but_serves_and_writes_nothing(settings_file, caplog, case):
    app = _app(settings_file)
    with TestClient(app):
        db_id, _ = _ids(app)
    st = app.state.storage
    if case == "bucket_missing":
        st.delete(instance.MARKER_KEY)
    elif case == "differ":
        instance.write_bucket_instance_id(st, "f" * 32)
    else:
        with app.state.sessions() as s:
            instance.delete_db_instance_id(s)
    before = _ids(app)
    caplog.set_level(logging.WARNING)
    app2 = _app(settings_file)
    with TestClient(app2) as client:
        assert client.get("/api/books").status_code == 200
        assert _ids(app2) == before
    msgs = _caplog_warn(caplog)
    assert msgs, caplog.text
    for x in before:
        if x:
            assert x in msgs[0]


def test_bucket_marker_parse(settings_file, tmp_path):
    from app.storage import LocalStorage

    st = LocalStorage(tmp_path, b"k" * 32)
    assert instance.bucket_instance_id(st) is None
    st.put(instance.MARKER_KEY, b"not json")
    with pytest.raises(instance.InstanceMarkerError):
        instance.bucket_instance_id(st)
    st.put(instance.MARKER_KEY, json.dumps({"storage_instance_id": ""}).encode())
    with pytest.raises(instance.InstanceMarkerError):
        instance.bucket_instance_id(st)
