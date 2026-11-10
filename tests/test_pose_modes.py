"""精準／快速角色動作模式的可用性：只讀 preflight 快取，不連 ComfyUI。"""

from fastapi.testclient import TestClient

from app import main as app_main
from app.config import load_settings
from app.services.pose_modes import availability, precise_unavailable_detail
from app.services.preflight import MissingModel, MissingNode, PreflightReport, WorkflowReport
from tests.fake_engine import FakeEngine

QWEN = "qwen_edit_pose_cutout"
SDXL = "sdxl_pose_cutout"
GGUF = "qwen-image-edit-2511-Q4_K_M.gguf"


def report(*workflows: WorkflowReport, reachable: bool = True) -> PreflightReport:
    return PreflightReport(ok=False, comfy_url="http://x", comfy_reachable=reachable, workflows=list(workflows))


def ok_wf(name: str) -> WorkflowReport:
    return WorkflowReport(name=name, ok=True, version="1")


def qwen_missing_gguf() -> WorkflowReport:
    return WorkflowReport(
        name=QWEN, ok=False, version="1",
        missing_models=[MissingModel("qwen_edit_2511_q4km", GGUF, "unet", "ComfyUI 的 unet 清單中沒有此檔案")],
    )


def both_available() -> dict:
    return {"precise": {"available": True, "missing": []}, "fast": {"available": True, "missing": []}}


def test_no_report_means_both_available():
    assert availability(None) == both_available()


def test_unreachable_without_load_errors_means_both_available():
    bad = WorkflowReport(name=QWEN, ok=False, version="1")
    assert availability(report(bad, ok_wf(SDXL), reachable=False)) == both_available()


def test_unreachable_but_load_errors_still_unavailable():
    bad = WorkflowReport(name=QWEN, ok=False, version="1", load_errors=["manifest 壞了"])
    res = availability(report(bad, ok_wf(SDXL), reachable=False))
    assert res["precise"] == {"available": False, "missing": ["manifest 壞了"]}
    assert res["fast"] == {"available": True, "missing": []}
    assert precise_unavailable_detail(report(bad, reachable=False)) == "精準模式缺少：manifest 壞了"


def test_all_ok():
    assert availability(report(ok_wf(QWEN), ok_wf(SDXL))) == both_available()


def test_qwen_missing_node_makes_precise_unavailable():
    wf = WorkflowReport(name=QWEN, ok=False, version="1",
                        missing_nodes=[MissingNode("UnetLoaderGGUF", ["1"], "https://github.com/city96/ComfyUI-GGUF")])
    res = availability(report(wf, ok_wf(SDXL)))
    assert res["precise"] == {"available": False, "missing": ["UnetLoaderGGUF"]}
    assert res["fast"] == {"available": True, "missing": []}


def test_missing_lists_models_nodes_and_load_errors():
    wf = WorkflowReport(
        name=QWEN, ok=False, version="1", load_errors=["workflow_api.json 讀不到"],
        missing_nodes=[MissingNode("UnetLoaderGGUF", ["1"])],
        missing_models=[MissingModel("m", GGUF, "unet", "r")],
    )
    res = availability(report(wf, ok_wf(SDXL)))
    assert res["precise"]["available"] is False
    assert set(res["precise"]["missing"]) == {GGUF, "UnetLoaderGGUF", "workflow_api.json 讀不到"}


def test_input_errors_only_still_names_something():
    wf = WorkflowReport(name=QWEN, ok=False, version="1", input_errors=["node 2: 缺少必填輸入 type"])
    res = availability(report(wf, ok_wf(SDXL)))
    assert res["precise"] == {"available": False, "missing": ["node 2: 缺少必填輸入 type"]}


def test_workflow_not_in_report_is_unavailable():
    res = availability(report(ok_wf(SDXL)))
    assert res["precise"]["available"] is False
    assert res["precise"]["missing"] == [f"workflow {QWEN}"]
    assert res["fast"]["available"] is True


def test_precise_unavailable_detail():
    assert precise_unavailable_detail(report(ok_wf(SDXL))) is not None
    assert precise_unavailable_detail(None) is None
    assert precise_unavailable_detail(report(ok_wf(QWEN), ok_wf(SDXL))) is None
    two = WorkflowReport(name=QWEN, ok=False, version="1", missing_nodes=[MissingNode("UnetLoaderGGUF", ["1"])],
                         missing_models=[MissingModel("m", GGUF, "unet", "r")])
    assert precise_unavailable_detail(report(two)) == f"精準模式缺少：{GGUF}、UnetLoaderGGUF"


def test_availability_accepts_other_workflow_mapping():
    """availability 可以換成別的 {模式: workflow} 對應（背景風格共用同一套規則）。"""
    mapping = {"a": QWEN, "b": "other_wf"}
    res = availability(report(qwen_missing_gguf(), ok_wf("other_wf")), mapping)
    assert res == {"a": {"available": False, "missing": [GGUF]}, "b": {"available": True, "missing": []}}
    assert availability(None, mapping) == {m: {"available": True, "missing": []} for m in mapping}


# ---- API ---------------------------------------------------------------------


def test_pose_modes_endpoint_reads_cache_and_preflight_refreshes_it(settings_file, monkeypatch):
    calls = []
    reports = [report(qwen_missing_gguf(), ok_wf(SDXL)), report(ok_wf(QWEN), ok_wf(SDXL))]

    async def fake_run_preflight(client, registry):
        calls.append(1)
        return reports[min(len(calls), len(reports)) - 1]

    monkeypatch.setattr(app_main, "run_preflight", fake_run_preflight)
    app = app_main.create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as client:
        assert len(calls) == 1  # 啟動時跑一次
        assert app.state.preflight is reports[0]
        body = client.get("/api/system/pose-modes").json()
        assert body == {"precise": {"available": False, "missing": [GGUF]},
                        "fast": {"available": True, "missing": []}}
        assert len(calls) == 1  # pose-modes 不會再跑 preflight（不連 ComfyUI）
        assert client.get("/api/system/preflight").status_code == 200
        assert len(calls) == 2 and app.state.preflight is reports[1]
        assert client.get("/api/system/pose-modes").json() == both_available()
        assert len(calls) == 2


def test_startup_preflight_crash_leaves_cache_empty(settings_file, monkeypatch):
    async def boom(client, registry):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_main, "run_preflight", boom)
    app = app_main.create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as client:  # 不可讓服務當機
        assert app.state.preflight is None
        assert client.get("/api/system/pose-modes").json() == both_available()
