"""Workflow 登錄：讀取 workflows_dir 下每個 workflow 資料夾（workflow_api.json + manifest.yaml）。

- manifest 先以 JSON schema 驗證，再對照 workflow 做語意檢查（title 唯一、輸入欄位存在且不是連線…）。
- 只有 manifest 白名單內的參數可以修改 prompt；其他節點輸入一律維持 workflow 原值。
- 節點以 `_meta.title` 定位，不寫死 node id。
- 單一 workflow 有問題時只標記該 workflow 的 errors，不影響其他 workflow 載入。

manifest 格式說明見 comfy_workflows/README.md。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml

MANIFEST_FILE = "manifest.yaml"
DEFAULT_WORKFLOW_FILE = "workflow_api.json"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "manifest.schema.json"


class ParameterError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("參數錯誤：" + "；".join(problems))


class WorkflowNotReady(RuntimeError):
    pass


@lru_cache(maxsize=1)
def manifest_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_link(value: Any) -> bool:
    """API 格式中，連線以 [來源 node id, 輸出 index] 表示。"""
    return (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
            and isinstance(value[1], int) and not isinstance(value[1], bool))


@dataclass(frozen=True)
class Target:
    title: str
    input: str


@dataclass
class ParamSpec:
    name: str
    type: str
    targets: list[Target]
    default: Any = None
    has_default: bool = False
    required: bool = False
    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    choices: list[Any] | None = None
    description: str = ""

    @classmethod
    def from_manifest(cls, name: str, d: dict) -> "ParamSpec":
        return cls(
            name=name, type=d["type"], targets=[Target(t["title"], t["input"]) for t in d["targets"]],
            default=d.get("default"), has_default="default" in d, required=d.get("required", False),
            min=d.get("min"), max=d.get("max"), max_length=d.get("max_length"), choices=d.get("choices"),
            description=d.get("description", ""),
        )

    def coerce(self, value: Any) -> Any:
        """驗證並轉換單一值；不合法時拋出 ValueError（訊息不含參數名稱）。"""
        t = self.type
        if t == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                else:
                    raise ValueError(f"需要整數，收到 {value!r}")
        elif t == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"需要數字，收到 {value!r}")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("數值必須是有限值")
        elif t == "string":
            if not isinstance(value, str):
                raise ValueError(f"需要字串，收到 {type(value).__name__}")
            if self.max_length is not None and len(value) > self.max_length:
                raise ValueError(f"長度 {len(value)} 超過上限 {self.max_length}")
        elif t == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"需要布林值，收到 {value!r}")
        elif t == "enum":
            if value not in (self.choices or []):
                raise ValueError(f"{value!r} 不在允許值 {self.choices} 之中")
        elif t == "image":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("需要已上傳到 ComfyUI 的圖片名稱")
            parts = value.replace("\\", "/").split("/")
            if value.startswith("/") or ".." in parts:
                raise ValueError(f"圖片名稱不合法：{value!r}")
        else:  # pragma: no cover - schema 已限制
            raise ValueError(f"未知型別 {t}")
        if t in ("int", "float"):
            if self.min is not None and value < self.min:
                raise ValueError(f"{value} 小於下限 {self.min}")
            if self.max is not None and value > self.max:
                raise ValueError(f"{value} 大於上限 {self.max}")
        return value


@dataclass
class BuiltPrompt:
    prompt: dict[str, Any]
    params: dict[str, Any]  # 實際生效的所有參數（含預設值與 workflow 原值）
    workflow_name: str
    workflow_version: str
    workflow_hash: str
    manifest_hash: str
    models: list[str]
    output_node_ids: list[str]


@dataclass
class WorkflowEntry:
    name: str
    dir: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    params: dict[str, ParamSpec] = field(default_factory=dict)
    workflow: dict[str, Any] | None = None
    workflow_hash: str | None = None
    manifest_hash: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.workflow is not None

    @property
    def version(self) -> str | None:
        if not (self.workflow_hash and self.manifest_hash):
            return None
        return hashlib.sha256((self.workflow_hash + self.manifest_hash).encode()).hexdigest()[:12]

    @property
    def models(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("models", []))

    def class_types(self) -> set[str]:
        return {n["class_type"] for n in (self.workflow or {}).values()}

    def nodes_by_title(self, title: str) -> list[str]:
        return [nid for nid, n in (self.workflow or {}).items() if (n.get("_meta") or {}).get("title") == title]

    def node_by_title(self, title: str) -> tuple[str, dict]:
        ids = self.nodes_by_title(title)
        if len(ids) != 1:
            raise KeyError(title)
        return ids[0], self.workflow[ids[0]]  # type: ignore[index]

    def build_prompt(self, values: dict[str, Any]) -> BuiltPrompt:
        """依白名單參數產生可送出的 prompt。不在 manifest 的參數一律拒絕。"""
        if not self.ok:
            raise WorkflowNotReady(f"workflow {self.name} 無法使用：" + "；".join(self.errors or ["缺 workflow 檔"]))
        problems: list[str] = []
        unknown = sorted(set(values) - set(self.params))
        if unknown:
            problems.append(f"不允許的參數：{', '.join(unknown)}（允許：{', '.join(sorted(self.params))}）")

        prompt = copy.deepcopy(self.workflow)
        resolved: dict[str, Any] = {}
        for name, spec in self.params.items():
            if name in values:
                raw = values[name]
            elif spec.has_default:
                raw = spec.default
            elif spec.required:
                problems.append(f"{name}: 必填")
                continue
            else:
                # 沒給值：維持 workflow 原值，但仍記錄下來
                nid, node = self.node_by_title(spec.targets[0].title)
                resolved[name] = node["inputs"][spec.targets[0].input]
                continue
            try:
                value = spec.coerce(raw)
            except ValueError as e:
                problems.append(f"{name}: {e}")
                continue
            resolved[name] = value
            for t in spec.targets:
                nid, _ = self.node_by_title(t.title)
                prompt[nid]["inputs"][t.input] = value
        if problems:
            raise ParameterError(problems)

        output_ids = [self.node_by_title(o["title"])[0] for o in self.manifest["outputs"]]
        return BuiltPrompt(
            prompt=prompt, params=resolved, workflow_name=self.name, workflow_version=self.version or "",
            workflow_hash=self.workflow_hash or "", manifest_hash=self.manifest_hash or "",
            models=[m["filename"] for m in self.models], output_node_ids=output_ids,
        )


def _validate_against_workflow(entry: WorkflowEntry) -> list[str]:
    wf = entry.workflow or {}
    errors: list[str] = []

    def check_title(title: str, where: str) -> tuple[str, dict] | None:
        ids = entry.nodes_by_title(title)
        if not ids:
            errors.append(f"{where}: workflow 內找不到 title 為「{title}」的節點")
            return None
        if len(ids) > 1:
            errors.append(f"{where}: title「{title}」重複（node {', '.join(ids)}），請在 ComfyUI 中改成唯一名稱")
            return None
        return ids[0], wf[ids[0]]

    seen: dict[tuple[str, str], str] = {}
    for name, spec in entry.params.items():
        for t in spec.targets:
            found = check_title(t.title, f"參數 {name}")
            if not found:
                continue
            nid, node = found
            if t.input not in node.get("inputs", {}):
                errors.append(f"參數 {name}: 節點「{t.title}」（{node['class_type']}）沒有輸入欄位 {t.input}")
            elif is_link(node["inputs"][t.input]):
                errors.append(f"參數 {name}: 節點「{t.title}」的 {t.input} 是連線，不能當參數覆寫")
            key = (nid, t.input)
            if key in seen:
                errors.append(f"參數 {name} 與 {seen[key]} 指向同一個輸入（{t.title}.{t.input}）")
            seen[key] = name
        if spec.has_default:
            try:
                spec.coerce(spec.default)
            except ValueError as e:
                errors.append(f"參數 {name} 的預設值不合法：{e}")

    for out in entry.manifest.get("outputs", []):
        check_title(out["title"], "outputs")

    for m in entry.models:
        loader = m.get("loader")
        if not loader:
            continue
        found = check_title(loader["title"], f"模型 {m['id']}")
        if not found:
            continue
        _, node = found
        actual = node.get("inputs", {}).get(loader["input"])
        if actual != m["filename"]:
            errors.append(
                f"模型 {m['id']}: 節點「{loader['title']}」的 {loader['input']} 為 {actual!r}，"
                f"與 manifest 的 {m['filename']!r} 不一致"
            )
    return errors


def load_workflow_dir(path: Path) -> WorkflowEntry:
    entry = WorkflowEntry(name=path.name, dir=path)
    manifest_path = path / MANIFEST_FILE
    if not manifest_path.is_file():
        entry.errors.append(f"缺少 {MANIFEST_FILE}")
        return entry
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        entry.errors.append(f"{MANIFEST_FILE} 解析失敗：{e}")
        return entry
    entry.manifest_hash = sha256_file(manifest_path)

    validator = jsonschema.Draft202012Validator(manifest_schema())
    schema_errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    if schema_errors:
        for e in schema_errors:
            loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
            entry.errors.append(f"manifest schema：{loc}: {e.message}")
        return entry

    entry.manifest = manifest
    entry.name = manifest["name"]
    if entry.name != path.name:
        entry.errors.append(f"manifest name「{entry.name}」與資料夾名稱「{path.name}」不同")
    entry.params = {n: ParamSpec.from_manifest(n, d) for n, d in manifest["parameters"].items()}

    wf_path = path / manifest.get("workflow_file", DEFAULT_WORKFLOW_FILE)
    if not wf_path.is_file():
        entry.errors.append(f"缺少 workflow 檔 {wf_path.name}")
        return entry
    try:
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        entry.errors.append(f"{wf_path.name} 不是合法 JSON：{e}")
        return entry
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        entry.errors.append(f"{wf_path.name} 是 UI 格式；請在 ComfyUI 用「Export (API)」匯出")
        return entry
    if not isinstance(wf, dict) or not all(isinstance(n, dict) and "class_type" in n for n in wf.values()):
        entry.errors.append(f"{wf_path.name} 不是 ComfyUI API 格式（每個節點需有 class_type）")
        return entry
    for nid, n in wf.items():
        n.setdefault("inputs", {})
        n.setdefault("_meta", {})
    entry.workflow = wf
    entry.workflow_hash = sha256_file(wf_path)
    entry.errors.extend(_validate_against_workflow(entry))
    return entry


class WorkflowRegistry:
    def __init__(self, workflows_dir: Path):
        self.workflows_dir = Path(workflows_dir)
        self.entries: dict[str, WorkflowEntry] = {}

    def load(self) -> "WorkflowRegistry":
        self.entries = {}
        for d in sorted(p for p in self.workflows_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
            if not (d / MANIFEST_FILE).exists() and not (d / DEFAULT_WORKFLOW_FILE).exists():
                continue
            entry = load_workflow_dir(d)
            if entry.name in self.entries:
                entry.errors.append(f"workflow 名稱「{entry.name}」重複")
                entry.name = d.name
            self.entries[entry.name] = entry
        return self

    def get(self, name: str) -> WorkflowEntry:
        try:
            return self.entries[name]
        except KeyError:
            raise KeyError(f"沒有名為 {name} 的 workflow（可用：{', '.join(self.entries) or '無'}）") from None

    def __iter__(self):
        return iter(self.entries.values())
