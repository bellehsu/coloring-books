from datetime import datetime, timedelta, timezone

from app.models import GenKind
from app.services.scheduling import QueuedJob, family_of, plan_order
from app.services.worker import encode_request

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)


def _rj(kind, pose_mode=None, background_style=None):
    return encode_request(kind=kind, prompt="p", negative_prompt="n", seed=1, width=8, height=8,
                          pose_mode=pose_mode, background_style=background_style)


def test_family_of():
    assert family_of(GenKind.POSE, _rj(GenKind.POSE, "precise")) == "qwen"
    assert family_of(GenKind.POSE, _rj(GenKind.POSE, "fast")) == "sdxl"
    assert family_of(GenKind.POSE, _rj(GenKind.POSE)) == "sdxl"  # 缺 pose_mode
    assert family_of(GenKind.BACKGROUND, _rj(GenKind.BACKGROUND)) == "sdxl"
    assert family_of(GenKind.POSE, "{not json") == "sdxl"
    assert family_of(GenKind.POSE, "[1, 2]") == "sdxl"  # 不是物件
    # kind 以參數為準：非 POSE 即使帶 precise 也是 sdxl
    assert family_of(GenKind.BACKGROUND, _rj(GenKind.POSE, "precise")) == "sdxl"


def test_family_of_background_style():
    B = GenKind.BACKGROUND
    assert family_of(B, _rj(B, background_style="simple")) == "qwen"
    assert family_of(B, _rj(B, background_style="detailed")) == "sdxl"
    assert family_of(B, _rj(B)) == "sdxl"  # 舊列沒有 background_style
    assert family_of(B, "{not json") == "sdxl"
    assert family_of(B, "[1, 2]") == "sdxl"
    # kind 以參數為準：非 BACKGROUND 帶 simple 不算 qwen
    assert family_of(GenKind.PAGE, _rj(B, background_style="simple")) == "sdxl"
    assert family_of(GenKind.CHARACTER, _rj(B, background_style="simple")) == "sdxl"


def _job(i, family, age_s):
    return QueuedJob(id=i, family=family, created_at=NOW - timedelta(seconds=age_s))


def test_plan_order_groups_same_family():
    queued = [_job(1, "sdxl", 40), _job(2, "qwen", 30), _job(3, "sdxl", 20), _job(4, "qwen", 10)]
    assert plan_order(queued, "qwen", NOW, 180) == [2, 4, 1, 3]
    assert plan_order(queued, "sdxl", NOW, 180) == [1, 3, 2, 4]


def test_plan_order_no_last_family_is_fifo():
    queued = [_job(3, "sdxl", 20), _job(1, "sdxl", 40), _job(4, "qwen", 10), _job(2, "qwen", 30)]
    assert plan_order(queued, None, NOW, 180) == [1, 2, 3, 4]
    assert plan_order(queued, "unknown", NOW, 180) == [1, 2, 3, 4]
    assert plan_order([], "qwen", NOW, 180) == []


def test_plan_order_ties_broken_by_id():
    queued = [_job(5, "sdxl", 10), _job(2, "sdxl", 10), _job(9, "qwen", 10), _job(1, "qwen", 10)]
    assert plan_order(queued, None, NOW, 180) == [1, 2, 5, 9]
    assert plan_order(queued, "sdxl", NOW, 180) == [2, 5, 1, 9]


def test_plan_order_overdue_first():
    queued = [_job(1, "sdxl", 200), _job(2, "qwen", 30), _job(3, "sdxl", 20), _job(4, "qwen", 10)]
    assert plan_order(queued, "qwen", NOW, 180) == [1, 2, 4, 3]


def test_plan_order_multiple_overdue_oldest_first():
    queued = [_job(1, "qwen", 190), _job(2, "sdxl", 300), _job(3, "sdxl", 20), _job(4, "qwen", 10)]
    assert plan_order(queued, "qwen", NOW, 180) == [2, 1, 4, 3]
    # 剛好等於門檻不算逾時（必須 >）
    assert plan_order([_job(1, "sdxl", 180), _job(2, "qwen", 10)], "qwen", NOW, 180) == [2, 1]


def test_plan_order_max_wait_zero_is_fifo():
    queued = [_job(1, "sdxl", 2), _job(2, "qwen", 1)]
    assert plan_order(queued, "qwen", NOW, 0) == [1, 2]
