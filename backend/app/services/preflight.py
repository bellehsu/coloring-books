"""Preflight：對照執行中 ComfyUI 的 /object_info 與模型清單，檢查每個 workflow 能不能跑。

回報（每個 workflow）：
  missing_nodes   —— workflow 用到但 ComfyUI 沒有的節點類別（附上 manifest 裡對應的自訂節點 repo）
  missing_models  —— manifest 列出但 ComfyUI 看不到的模型檔
  input_errors    —— 必填輸入缺漏、下拉選單值不在選項內、enum 參數的 choices 不被節點接受
  input_warnings  —— workflow 有、但節點定義沒有的輸入欄位（ComfyUI 會忽略）
另外（不靠 ComfyUI）：caption_font —— 頁面文字的字型檔能不能用。這是獨立的一項，不影響 ok：
缺字型只會讓「有頁面文字」的頁面匯出失敗（409），其他功能照常。
任何例外都轉成報告內容，不讓呼叫端當機。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .captions import check_caption_font
from .comfy_client import ComfyClient, ComfyError
from .workflow_registry import WorkflowEntry, WorkflowRegistry, is_link


@dataclass
class MissingNode:
    class_type: str
    node_ids: list[str]
    provided_by: str | None = None  # 自訂節點 repo


@dataclass
class MissingModel:
    id: str
    filename: str
    folder: str
    reason: str
    source: str | None = None


@dataclass
class WorkflowReport:
    name: str
    ok: bool
    version: str | None
    load_errors: list[str] = field(default_factory=list)
    missing_nodes: list[MissingNode] = field(default_factory=list)
    missing_models: list[MissingModel] = field(default_factory=list)
    input_errors: list[str] = field(default_factory=list)
    input_warnings: list[str] = field(default_factory=list)


@dataclass
class CaptionFontReport:
    path: str | None
    ok: bool
    error: str | None = None


@dataclass
class PreflightReport:
    ok: bool
    comfy_url: str
    comfy_reachable: bool
    comfy_error: str | None = None
    comfy_version: str | None = None
    devices: list[dict] = field(default_factory=list)
    workflows: list[WorkflowReport] = field(default_factory=list)
    caption_font: CaptionFontReport | None = None  # 獨立項目，不計入 ok
    reload_error: str | None = None  # 重新載入 workflow 失敗時的原因（改用舊的登錄檢查）；有值時 ok 一定是 False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def combo_options(spec: Any) -> list[Any] | None:
    """從 /object_info 的輸入定義取出下拉選項；不是下拉選單時回傳 None。

    舊格式：[["a","b"], {...}]；新格式：["COMBO", {"options": ["a","b"]}]
    """
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    head = spec[0]
    if isinstance(head, list):
        return head
    if head == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return list(spec[1].get("options", []))
    return None


def _input_spec(object_info: dict, class_type: str, input_name: str) -> Any:
    inputs = object_info.get(class_type, {}).get("input", {})
    for section in ("required", "optional"):
        if input_name in (inputs.get(section) or {}):
            return inputs[section][input_name]
    return None


def _check_inputs(entry: WorkflowEntry, object_info: dict, rep: WorkflowReport) -> None:
    targeted: dict[tuple[str, str], Any] = {}
    for spec in entry.params.values():
        for t in spec.targets:
            ids = entry.nodes_by_title(t.title)
            if len(ids) == 1:
                targeted[(ids[0], t.input)] = spec
    # 模型載入欄位已由 _check_models 回報，不重複列為輸入錯誤
    model_inputs: set[tuple[str, str]] = set()
    for m in entry.models:
        if m.get("loader"):
            ids = entry.nodes_by_title(m["loader"]["title"])
            if len(ids) == 1:
                model_inputs.add((ids[0], m["loader"]["input"]))

    for nid, node in (entry.workflow or {}).items():
        ct = node["class_type"]
        info = object_info.get(ct)
        if info is None:
            continue
        label = f"node {nid}「{node['_meta'].get('title', ct)}」({ct})"
        defined = info.get("input", {})
        required = defined.get("required") or {}
        optional = defined.get("optional") or {}
        hidden = defined.get("hidden") or {}
        given = node.get("inputs", {})
        for name in required:
            if name not in given:
                rep.input_errors.append(f"{label}: 缺少必填輸入 {name}")
        for name, value in given.items():
            if name not in required and name not in optional and name not in hidden:
                rep.input_warnings.append(f"{label}: 節點定義沒有輸入 {name}（ComfyUI 會忽略）")
                continue
            options = combo_options(required.get(name) or optional.get(name))
            if options is None or is_link(value) or (nid, name) in model_inputs:
                continue
            param = targeted.get((nid, name))
            if param is not None:
                if param.type == "image":
                    continue  # 執行時才上傳
                if param.type == "enum":
                    bad = [c for c in param.choices or [] if c not in options]
                    if bad:
                        rep.input_errors.append(f"{label}: 參數 {param.name} 的 choices {bad} 不在節點選項內")
                    continue
            if value not in options and ct not in ("LoadImage",):
                rep.input_errors.append(f"{label}: {name}={value!r} 不在選項內")


async def _check_models(entry: WorkflowEntry, object_info: dict, client: ComfyClient,
                        cache: dict[str, list[str] | None], rep: WorkflowReport) -> None:
    for m in entry.models:
        options: list[Any] | None = None
        loader = m.get("loader")
        if loader and entry.workflow:
            ids = entry.nodes_by_title(loader["title"])
            if len(ids) == 1:
                ct = entry.workflow[ids[0]]["class_type"]
                if ct in object_info:
                    options = combo_options(_input_spec(object_info, ct, loader["input"]))
        if options is None:
            folder = m.get("comfy_folder", m["folder"])
            if folder not in cache:
                cache[folder] = await client.list_models(folder)
            options = cache[folder]
            if options is None:
                rep.missing_models.append(MissingModel(
                    m["id"], m["filename"], folder,
                    f"ComfyUI 沒有註冊資料夾類型「{folder}」（自訂節點未安裝或 extra_model_paths 未設定）",
                    m.get("source")))
                continue
        if m["filename"] not in options:
            rep.missing_models.append(MissingModel(
                m["id"], m["filename"], m["folder"], f"ComfyUI 的 {m['folder']} 清單中沒有此檔案", m.get("source")))


def check_font(path: Path | None) -> CaptionFontReport:
    try:
        error = check_caption_font(path)
    except Exception as e:  # noqa: BLE001 - preflight 不可讓服務當機
        error = f"{type(e).__name__}: {e}"
    return CaptionFontReport(str(path) if path is not None else None, error is None, error)


async def _check_entry(entry, object_info: dict, client: ComfyClient,
                       folder_cache: dict[str, list[str] | None], rep: WorkflowReport) -> None:
    provided_by = {ct: cn["repo"] for cn in entry.manifest.get("custom_nodes", []) for ct in cn["provides"]}
    missing: dict[str, list[str]] = {}
    for nid, node in entry.workflow.items():
        if node["class_type"] not in object_info:
            missing.setdefault(node["class_type"], []).append(nid)
    rep.missing_nodes = [MissingNode(ct, ids, provided_by.get(ct)) for ct, ids in sorted(missing.items())]
    try:
        await _check_models(entry, object_info, client, folder_cache, rep)
    except ComfyError as e:
        rep.load_errors.append(f"模型檢查失敗：{e}")
    _check_inputs(entry, object_info, rep)


async def run_preflight(client: ComfyClient, registry: WorkflowRegistry,
                        caption_font: Path | None = None) -> PreflightReport:
    report = PreflightReport(ok=False, comfy_url=client.base_url, comfy_reachable=False)
    report.caption_font = check_font(caption_font)
    object_info: dict | None = None
    try:
        stats = await client.system_stats()
        report.comfy_version = stats["system"].get("comfyui_version")
        report.devices = stats.get("devices", [])
        object_info = await client.object_info()
        report.comfy_reachable = True
    except ComfyError as e:
        report.comfy_error = str(e)
    except Exception as e:  # noqa: BLE001 - preflight 不可讓服務當機
        report.comfy_error = f"{type(e).__name__}: {e}"

    folder_cache: dict[str, list[str] | None] = {}
    for entry in registry:
        rep = WorkflowReport(name=entry.name, ok=False, version=entry.version, load_errors=list(entry.errors))
        report.workflows.append(rep)
        if object_info is None or entry.workflow is None:
            continue
        try:
            await _check_entry(entry, object_info, client, folder_cache, rep)
        except Exception as e:  # noqa: BLE001 - 單一 workflow 檢查失敗只記在它自己的報告，不讓整份報告變 500
            rep.load_errors.append(f"檢查失敗：{type(e).__name__}: {e}")
        rep.ok = not (rep.load_errors or rep.missing_nodes or rep.missing_models or rep.input_errors)

    report.ok = report.comfy_reachable and bool(report.workflows) and all(w.ok for w in report.workflows)
    return report


def format_report(report: PreflightReport) -> str:
    lines = [f"ComfyUI：{report.comfy_url} — " + (
        f"可連線（{report.comfy_version}）" if report.comfy_reachable else f"無法連線：{report.comfy_error}")]
    for w in report.workflows:
        lines.append(f"\n[{'OK' if w.ok else 'NG'}] {w.name}（version {w.version or '-'}）")
        for e in w.load_errors:
            lines.append(f"  載入錯誤：{e}")
        for n in w.missing_nodes:
            hint = f" ← 安裝 {n.provided_by}" if n.provided_by else ""
            lines.append(f"  缺節點：{n.class_type}（node {', '.join(n.node_ids)}）{hint}")
        for m in w.missing_models:
            src = f"\n      來源：{m.source}" if m.source else ""
            lines.append(f"  缺模型：{m.folder}/{m.filename} — {m.reason}{src}")
        for e in w.input_errors:
            lines.append(f"  輸入錯誤：{e}")
        for e in w.input_warnings:
            lines.append(f"  輸入警告：{e}")
        if not report.comfy_reachable and not w.load_errors:
            lines.append("  （ComfyUI 無法連線，未檢查節點與模型）")
    f = report.caption_font
    if f is not None:
        lines.append(f"\n[{'OK' if f.ok else 'NG'}] 頁面文字字型：{f.path or '-'}")
        if not f.ok:
            lines.append(f"  {f.error}（有頁面文字的頁面無法匯出；其他功能不受影響）")
    lines.append("\n結果：" + ("全部通過" if report.ok else "有項目未通過"))
    return "\n".join(lines)
