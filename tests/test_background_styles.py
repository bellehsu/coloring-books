"""背景風格（簡潔 Qwen／細緻 SDXL）：排隊時的風格決定與可用性（只讀 preflight 快取）。"""

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.config import load_settings
from app.services.background_styles import decide_style, style_availability
from app.services.preflight import MissingModel, MissingNode, PreflightReport, WorkflowReport
from tests.fake_engine import FakeEngine

QWEN_BG = "qwen_background"
SDXL_BG = "sdxl_background"
GGUF = "qwen-image-edit-2511-Q4_K_M.gguf"


def report(*workflows: WorkflowReport, reachable: bool = True) -> PreflightReport:
    return PreflightReport(ok=False, comfy_url="http://x", comfy_reachable=reachable, workflows=list(workflows))


def ok_wf(name: str) -> WorkflowReport:
    return WorkflowReport(name=name, ok=True, version="1")


def qwen_bg_missing() -> WorkflowReport:
    return WorkflowReport(
        name=QWEN_BG, ok=False, version="1",
        missing_models=[MissingModel("qwen_edit_2511_q4km", GGUF, "unet", "沒有此檔案")],
        missing_nodes=[MissingNode("UnetLoaderGGUF", ["1"])],
    )


BOTH = {"simple": {"available": True, "missing": []}, "detailed": {"available": True, "missing": []}}


@pytest.mark.parametrize(
    ("requested", "has_usable_sketch", "simple_available", "expected"),
    [
        ("simple", True, True, "detailed"),  # 有可用草圖 → 細緻（才會照草圖構圖）
        ("simple", False, True, "simple"),
        ("simple", False, False, "detailed"),  # 簡潔不可用 → 退回細緻
        ("detailed", False, True, "detailed"),
        ("detailed", True, False, "detailed"),
    ],
)
def test_decide_style(requested, has_usable_sketch, simple_available, expected):
    assert decide_style(
        requested, has_usable_sketch=has_usable_sketch, simple_available=simple_available
    ) == expected


def test_style_availability_rules():
    assert style_availability(None) == BOTH
    assert style_availability(report(ok_wf(QWEN_BG), ok_wf(SDXL_BG))) == BOTH
    # 連不上 ComfyUI：狀態未知 → 算可用
    assert style_availability(report(qwen_bg_missing(), ok_wf(SDXL_BG), reachable=False)) == BOTH
    res = style_availability(report(qwen_bg_missing(), ok_wf(SDXL_BG)))
    assert res["simple"] == {"available": False, "missing": [GGUF, "UnetLoaderGGUF"]}
    assert res["detailed"] == {"available": True, "missing": []}
    # workflow 不在報告裡 → 不可用
    res = style_availability(report(ok_wf(QWEN_BG)))
    assert res["detailed"] == {"available": False, "missing": [f"workflow {SDXL_BG}"]}


# ---- API ---------------------------------------------------------------------


def test_background_styles_endpoint(settings_file, monkeypatch):
    calls = []
    reports = [report(qwen_bg_missing(), ok_wf(SDXL_BG)), report(ok_wf(QWEN_BG), ok_wf(SDXL_BG))]

    async def fake_run_preflight(client, registry):
        calls.append(1)
        return reports[min(len(calls), len(reports)) - 1]

    monkeypatch.setattr(app_main, "run_preflight", fake_run_preflight)
    app = app_main.create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as client:
        body = client.get("/api/system/background-styles").json()
        assert body == {"simple": {"available": False, "missing": [GGUF, "UnetLoaderGGUF"]},
                        "detailed": {"available": True, "missing": []}}
        assert len(calls) == 1  # 不會再跑 preflight（不連 ComfyUI）
        assert client.get("/api/system/preflight").status_code == 200  # 重新整理快取
        assert client.get("/api/system/background-styles").json() == BOTH
        assert len(calls) == 2
        app.state.preflight = None  # 沒有報告 → 兩種都算可用
        assert client.get("/api/system/background-styles").json() == BOTH
