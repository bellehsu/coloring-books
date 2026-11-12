#!/usr/bin/env python3
"""實驗矩陣：路線 × 線稿方式 × 場景 × seeds，依序生成、後處理、計算指標。

用法：
  python experiments/run_matrix.py run experiments/configs/phase1_example.yaml [--dry-run] [--limit N]
  python experiments/run_matrix.py run CONFIG --resume experiments/results/<run_dir>   # 跳過已成功的格子
  python experiments/run_matrix.py report experiments/results/<run_dir>               # 重新產生 report.html
  python experiments/run_matrix.py summarize experiments/results/<run_dir> [--scores scores.csv]
  python experiments/run_matrix.py reextract experiments/results/<run_dir> --config CONFIG  # 對既有原圖重新擷取線稿

線稿方式可加 extract（Route C：先生成彩色圖再擷取線條，見 experiments/lineart_methods.py）：
  lineart_methods:
    color_xdog: {prompt: "...", extract: {method: xdog, sigma: 0.8}}
同一次執行中，workflow 版本與參數完全相同的格子只生成一次，其餘共用原圖（gen_source 記錄來源格子）。

輸出（experiments/results/<name>_<時間>/）：
  results.csv   每一格一列：參數、workflow 版本、耗時、錯誤、指標
  run.json      實驗設定與 workflow hash
  report.html   對照頁；每張圖旁邊可填一致性分數（1–5）與備註，按「匯出分數 CSV」存成 scores.csv
  summary.md / summary.csv   summarize 之後產生（含一致性分數彙總）
  raw/ processed/            原始輸出與 1-bit 線稿

設定檔格式見 experiments/configs/phase1_example.yaml。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.config import PROJECT_ROOT, SettingsError, load_settings  # noqa: E402
from app.services.comfy_client import ComfyClient, ComfyError  # noqa: E402
from app.services.lineart_post import LineartParams, load_image, process, save_1bit_png  # noqa: E402
from app.services.preflight import format_report, run_preflight  # noqa: E402
from app.services.workflow_registry import ParameterError, WorkflowEntry, WorkflowRegistry  # noqa: E402
from experiments.lineart_methods import ExtractSpec, extract  # noqa: E402
from experiments.metrics import METRIC_KEYS, load_scores, summarize, summary_markdown, write_csv  # noqa: E402

UPLOAD_SUBFOLDER = "coloring_books"
RESULT_FIELDS = [
    "cell_id", "scene", "route", "lineart", "seed", "status", "error",
    "workflow", "workflow_version", "workflow_hash", "models",
    "elapsed_s", "postprocess_s", "prompt_id", "started_at", "gen_source",
    "extract_method", "extract_params", "extract_s",
    "raw_image", "processed_image", *METRIC_KEYS, "params_json",
]


class ConfigError(ValueError):
    pass


# ---- 設定 -------------------------------------------------------------------------

@dataclass
class Cell:
    scene: str
    route: str
    lineart: str
    seed: int
    params: dict[str, Any]  # 尚未上傳圖片前的參數（圖片為本機路徑）
    postprocess: dict[str, Any]
    extract: dict[str, Any] | None = None  # None = 不擷取（method none）

    @property
    def cell_id(self) -> str:
        return f"{self.scene}__{self.route}__{self.lineart}__s{self.seed}"


@dataclass
class MatrixConfig:
    name: str
    raw: dict[str, Any]
    routes: dict[str, dict[str, Any]]
    lineart_methods: dict[str, dict[str, Any]]
    scenes: dict[str, dict[str, Any]]
    seeds: list[int]
    character: dict[str, Any] = field(default_factory=dict)
    postprocess: dict[str, Any] = field(default_factory=dict)
    output_dir: Path | None = None
    comfy_url: str | None = None


def _require_mapping(d: Any, where: str) -> dict:
    if not isinstance(d, dict) or not d:
        raise ConfigError(f"{where} 必須是非空的 mapping")
    return d


def load_config(path: Path) -> MatrixConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("設定檔頂層必須是 mapping")
    routes = _require_mapping(raw.get("routes"), "routes")
    for k, r in routes.items():
        if not isinstance(r, dict) or "workflow" not in r:
            raise ConfigError(f"routes.{k} 必須包含 workflow")
        r.setdefault("seed_param", "seed")
    lineart_methods = _require_mapping(raw.get("lineart_methods"), "lineart_methods")
    for k, la in lineart_methods.items():
        if isinstance(la, dict) and la.get("extract") is not None:
            try:
                ExtractSpec.from_dict(la["extract"])
            except (ValueError, TypeError) as e:
                raise ConfigError(f"lineart_methods.{k}.extract：{e}") from e
    seeds = raw.get("seeds")
    if not isinstance(seeds, list) or not seeds or not all(isinstance(s, int) and not isinstance(s, bool) for s in seeds):
        raise ConfigError("seeds 必須是非空的整數清單")
    return MatrixConfig(
        name=str(raw.get("name") or path.stem),
        raw=raw,
        routes=routes,
        lineart_methods=lineart_methods,
        scenes=_require_mapping(raw.get("scenes"), "scenes"),
        seeds=seeds,
        character=raw.get("character") or {},
        postprocess=raw.get("postprocess") or {},
        output_dir=Path(raw["output_dir"]) if raw.get("output_dir") else None,
        comfy_url=raw.get("comfy_url"),
    )


_PLACEHOLDER = re.compile(r"\{(character|character_image|scene|lineart)\}")


def _fill(value: Any, ctx: dict[str, str]) -> Any:
    """只替換 {character} {character_image} {scene} {lineart} 四種佔位符，其他大括號原樣保留。"""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: ctx.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _fill(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ctx) for v in value]
    return value


def _layer(section: dict[str, Any], route: str) -> dict[str, Any]:
    out = dict(section.get("params") or {})
    out.update((section.get("route_params") or {}).get(route) or {})
    return out


def expand_cells(cfg: MatrixConfig) -> list[Cell]:
    """順序：路線最外層（減少模型切換）→ 線稿方式 → 場景 → seed。"""
    cells: list[Cell] = []
    for route_key, route in cfg.routes.items():
        for la_key, la in cfg.lineart_methods.items():
            if la.get("routes") and route_key not in la["routes"]:
                continue
            for sc_key, sc in cfg.scenes.items():
                ctx = {
                    "character": str(cfg.character.get("description", "")),
                    "character_image": str(cfg.character.get("reference_image", "")),
                    "scene": str(sc.get("prompt", "")),
                    "lineart": str(la.get("prompt", "")),
                }
                params: dict[str, Any] = {}
                params.update(route.get("params") or {})
                params.update(_layer(la, route_key))
                params.update(_layer(sc, route_key))
                params = _fill(params, ctx)
                post = {**cfg.postprocess, **(la.get("postprocess") or {})}
                for seed in cfg.seeds:
                    cells.append(Cell(sc_key, route_key, la_key, seed,
                                      {**params, route["seed_param"]: seed}, post, la.get("extract")))
    return cells


# ---- 執行 -------------------------------------------------------------------------

class ImageUploader:
    """以內容 hash 命名上傳，同一張圖只上傳一次。"""

    def __init__(self, client: ComfyClient):
        self.client = client
        self.cache: dict[Path, str] = {}

    async def ref_for(self, path: Path) -> str:
        path = path.resolve()
        if path not in self.cache:
            if not path.is_file():
                raise FileNotFoundError(f"找不到圖片 {path}")
            data = path.read_bytes()
            name = f"{hashlib.sha256(data).hexdigest()[:12]}_{path.name}"
            res = await self.client.upload_image(data, name, subfolder=UPLOAD_SUBFOLDER)
            self.cache[path] = self.client.uploaded_ref(res)
        return self.cache[path]


def _resolve_local(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


async def materialize_params(entry: WorkflowEntry, params: dict[str, Any],
                             uploader: ImageUploader | None) -> dict[str, Any]:
    """把 image 型別參數從本機路徑換成 ComfyUI 中的名稱（dry-run 時只檢查檔案存在）。"""
    out = dict(params)
    for name, spec in entry.params.items():
        if spec.type != "image" or name not in out or not isinstance(out[name], str):
            continue
        local = _resolve_local(out[name])
        if uploader is None:
            if not local.is_file():
                raise FileNotFoundError(f"參數 {name} 的圖片不存在：{local}")
            out[name] = f"{UPLOAD_SUBFOLDER}/dryrun_{local.name}"
        else:
            out[name] = await uploader.ref_for(local)
    return out


def _empty_row(cell: Cell) -> dict[str, Any]:
    return {"cell_id": cell.cell_id, "scene": cell.scene, "route": cell.route, "lineart": cell.lineart,
            "seed": cell.seed, "status": "error", "error": "", "started_at": datetime.now().isoformat(timespec="seconds")}


def _round_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in metrics.items()}


async def postprocess_cell(raw_path: Path, cell_id: str, extract_spec: dict[str, Any] | None,
                           postprocess: dict[str, Any], run_dir: Path, row: dict[str, Any]) -> None:
    """（可選）擷取線稿 → 後處理成 1-bit PNG → 指標，結果寫進 row。"""
    spec = ExtractSpec.from_dict(extract_spec)
    pp = LineartParams.from_dict(postprocess)
    row["extract_method"] = spec.method
    row["extract_params"] = json.dumps(spec.params, sort_keys=True)
    img = load_image(raw_path)
    t0 = time.monotonic()
    src = await asyncio.to_thread(extract, img, spec) if spec.method != "none" else img
    row["extract_s"] = round(time.monotonic() - t0, 2)
    t0 = time.monotonic()
    res = await asyncio.to_thread(process, src, pp)
    proc_path = save_1bit_png(res.ink, run_dir / "processed" / f"{cell_id}.png", dpi=pp.dpi)
    row["postprocess_s"] = round(time.monotonic() - t0, 2)
    row["processed_image"] = proc_path.relative_to(run_dir).as_posix()
    row.update(_round_metrics(res.metrics))


def gen_key(workflow_version: str, params_json: str) -> str:
    return f"{workflow_version}|{params_json}"


async def run_cell(cell: Cell, entry: WorkflowEntry, client: ComfyClient, uploader: ImageUploader,
                   run_dir: Path, timeout_s: float, gen_cache: dict[str, dict[str, Any]] | None = None
                   ) -> dict[str, Any]:
    row = _empty_row(cell)
    row.update(workflow=entry.name, workflow_version=entry.version, workflow_hash=entry.workflow_hash,
               models=";".join(m["filename"] for m in entry.models))
    try:
        params = await materialize_params(entry, cell.params, uploader)
        built = entry.build_prompt(params)
        row["params_json"] = json.dumps(built.params, ensure_ascii=False, sort_keys=True)
        key = gen_key(entry.version, row["params_json"])
        cached = (gen_cache or {}).get(key)
        if cached and (run_dir / cached["raw_image"]).is_file():
            row.update(gen_source=cached["cell_id"], raw_image=cached["raw_image"],
                       elapsed_s=cached["elapsed_s"], prompt_id=cached["prompt_id"])
            raw_path = run_dir / cached["raw_image"]
        else:
            def on_progress(ev):
                if ev.type == "progress" and ev.max:
                    print(f"\r    {ev.value}/{ev.max}", end="", flush=True)

            result = await client.run(built.prompt, timeout_s=timeout_s, on_progress=on_progress)
            print("\r", end="")
            row["prompt_id"] = result.prompt_id
            row["elapsed_s"] = round(result.elapsed_s, 2)
            files = [f for f in result.files if f.node_id in built.output_node_ids and f.type == "output"] \
                or result.files
            if not files:
                raise ComfyError("workflow 沒有輸出圖片")
            raw_path = run_dir / "raw" / f"{cell.cell_id}.png"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(await client.download(files[0]))
            row["raw_image"] = raw_path.relative_to(run_dir).as_posix()
            if gen_cache is not None:
                gen_cache[key] = {"cell_id": cell.cell_id, "raw_image": row["raw_image"],
                                  "elapsed_s": row["elapsed_s"], "prompt_id": row["prompt_id"]}

        await postprocess_cell(raw_path, cell.cell_id, cell.extract, cell.postprocess, run_dir, row)
        row["status"] = "ok"
    except (ComfyError, ParameterError, FileNotFoundError, ValueError, OSError) as e:
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def run_matrix(cfg: MatrixConfig, config_path: Path, *, settings_path: Path | None, dry_run: bool,
                     limit: int | None, resume: Path | None, only_routes: list[str] | None) -> int:
    try:
        settings = load_settings(settings_path)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    registry = WorkflowRegistry(settings.paths.workflows_dir).load()
    cells = expand_cells(cfg)
    if only_routes:
        cells = [c for c in cells if c.route in only_routes]
    if limit:
        cells = cells[:limit]

    entries: dict[str, WorkflowEntry] = {}
    for key, route in cfg.routes.items():
        try:
            entries[key] = registry.get(route["workflow"])
        except KeyError as e:
            print(f"錯誤：routes.{key}: {e}", file=sys.stderr)
            return 2

    if dry_run:
        bad = 0
        for c in cells:
            entry = entries[c.route]
            try:
                built = entry.build_prompt(await materialize_params(entry, c.params, None))
                print(f"[ok]  {c.cell_id}  ({entry.name} v{entry.version})")
                for k, v in built.params.items():
                    print(f"        {k} = {str(v)[:120]!s}")
                if c.extract:
                    print(f"        [extract] {ExtractSpec.from_dict(c.extract).to_dict()}")
            except Exception as e:  # noqa: BLE001
                bad += 1
                print(f"[NG]  {c.cell_id}: {e}")
        print(f"\n共 {len(cells)} 格，{bad} 格有問題。（dry-run 不連線 ComfyUI）")
        return 1 if bad else 0

    out_root = cfg.output_dir or (PROJECT_ROOT / "experiments" / "results")
    out_root = out_root if out_root.is_absolute() else PROJECT_ROOT / out_root
    if resume:
        run_dir = resume.resolve()
        rows = {r["cell_id"]: r for r in _read_rows(run_dir / "results.csv")}
    else:
        run_dir = out_root / f"{cfg.name}_{datetime.now():%Y%m%d_%H%M%S}"
        rows = {}
    # 已成功且自己生成（非共用）的格子 → 生成快取，接續時不重跑
    gen_cache: dict[str, dict[str, Any]] = {
        gen_key(r["workflow_version"], r["params_json"]): {
            "cell_id": r["cell_id"], "raw_image": r["raw_image"], "elapsed_s": r["elapsed_s"],
            "prompt_id": r["prompt_id"]}
        for r in rows.values()
        if r.get("status") == "ok" and not r.get("gen_source") and r.get("raw_image") and r.get("params_json")}

    url = cfg.comfy_url or settings.comfy.url
    async with ComfyClient(url, request_timeout_s=settings.comfy.request_timeout_s) as client:
        # 只檢查這次會跑到的路線，未使用的 workflow（例如尚未匯出的 Route B）不列入
        used = {entries[c.route].name for c in cells}
        registry.entries = {k: v for k, v in registry.entries.items() if k in used}
        report = await run_preflight(client, registry)
        print(format_report(report))
        if not report.comfy_reachable:
            return 1
        run_dir.mkdir(parents=True, exist_ok=True)  # ComfyUI 可連線才建立，避免留下空資料夾
        ok_workflows = {w.name for w in report.workflows if w.ok}
        meta = {
            "name": cfg.name, "config_file": str(config_path), "config": cfg.raw, "comfy_url": url,
            "comfy_version": report.comfy_version, "started_at": datetime.now().isoformat(timespec="seconds"),
            "workflows": {k: {"name": e.name, "version": e.version, "workflow_hash": e.workflow_hash,
                              "manifest_hash": e.manifest_hash, "models": [m["filename"] for m in e.models]}
                          for k, e in entries.items()},
        }
        (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        uploader = ImageUploader(client)
        prev_route = None
        aborted = False
        for i, cell in enumerate(cells, 1):
            if rows.get(cell.cell_id, {}).get("status") == "ok":
                continue
            entry = entries[cell.route]
            print(f"[{i}/{len(cells)}] {cell.cell_id}")
            if entry.name not in ok_workflows:
                row = _empty_row(cell)
                row.update(status="skipped", workflow=entry.name, error=f"workflow {entry.name} 未通過 preflight")
            else:
                if prev_route is not None and prev_route != cell.route:
                    try:
                        await client.free_memory()  # 換路線時釋放 VRAM
                    except ComfyError:
                        pass
                prev_route = cell.route
                row = await run_cell(cell, entry, client, uploader, run_dir, settings.comfy.run_timeout_s,
                                     gen_cache)
            rows[cell.cell_id] = row
            if row["status"] != "ok":
                print(f"    -> {row['status']}: {row['error']}")
            else:
                reuse = f"（共用 {row['gen_source']}）" if row.get("gen_source") else ""
                print(f"    -> ok {row['elapsed_s']}s{reuse}  extract={row.get('extract_method')}  "
                      f"closed_regions={row.get('closed_region_count')}")
            # 每格完成就寫檔，中途停止也不會遺失
            write_csv(list(rows.values()), run_dir / "results.csv", RESULT_FIELDS)
            # ComfyUI 中途停止時立刻結束，不要把剩下的格子全部記成失敗
            if row["status"] == "error" and row["error"].startswith("ComfyConnectionError"):
                try:
                    await client.system_stats()
                except ComfyError:
                    aborted = True
                    print(f"\nComfyUI（{url}）已無法連線，停止執行。重新啟動 ComfyUI 後接續：\n"
                          f"  python experiments/run_matrix.py run {config_path} --resume {run_dir}")
                    break

    all_rows = list(rows.values())
    write_csv(all_rows, run_dir / "results.csv", RESULT_FIELDS)
    write_report(run_dir, cfg.name, all_rows, cfg)
    n_ok = sum(r["status"] == "ok" for r in all_rows)
    if aborted:
        print(f"\n中斷：{n_ok}/{len(cells)} 格成功。")
        return 1
    print(f"\n完成：{n_ok}/{len(all_rows)} 格成功。")
    print(f"結果：{run_dir / 'results.csv'}")
    print(f"對照頁：{run_dir / 'report.html'}")
    return 0 if n_ok == len(all_rows) else 1


# ---- 報告 -------------------------------------------------------------------------

_REPORT_CSS = """
body{font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;margin:16px;background:#fafafa;color:#222}
h1{font-size:20px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #ccc}
.bar{position:sticky;top:0;background:#fafafa;padding:8px 0;z-index:2;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
table{border-collapse:collapse;margin-top:8px}td,th{border:1px solid #ddd;padding:6px;vertical-align:top;background:#fff}
th{background:#f0f0f0;font-size:13px}.cell{width:230px}.cell img{width:220px;display:block;cursor:zoom-in;border:1px solid #eee}
.m{font:11px/1.35 ui-monospace,monospace;color:#555;margin:4px 0}.err{color:#b00020;font-size:12px;white-space:pre-wrap}
.skip{color:#777;font-size:12px}label{font-size:12px}input[type=number]{width:52px}textarea{width:214px;height:38px;font-size:12px}
.ref img{max-height:220px;border:1px solid #ddd}.hint{font-size:12px;color:#666}
"""

_REPORT_JS = """
const KEY='scores:'+document.body.dataset.run;
function load(){try{return JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){return {}}}
function save(s){try{localStorage.setItem(KEY,JSON.stringify(s))}catch(e){}}
const s=load();
document.querySelectorAll('[data-cell]').forEach(el=>{
  const id=el.dataset.cell, f=el.dataset.field;
  if(s[id]&&s[id][f]!==undefined) el.value=s[id][f];
  el.addEventListener('input',()=>{const t=load();t[id]=t[id]||{};t[id][f]=el.value;save(t);count();});
});
document.querySelectorAll('img[data-alt]').forEach(img=>img.addEventListener('click',()=>{
  const a=img.dataset.alt;img.dataset.alt=img.getAttribute('src');img.setAttribute('src',a);
}));
function q(v){v=String(v??'');return /[",\\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v}
function count(){const t=load();const n=Object.values(t).filter(x=>x.consistency_score).length;
  document.getElementById('cnt').textContent=n+' / '+document.querySelectorAll('[data-field=consistency_score]').length+' 已評分';}
document.getElementById('export').addEventListener('click',()=>{
  const t=load();const rows=[['cell_id','consistency_score','notes']];
  document.querySelectorAll('[data-field=consistency_score]').forEach(el=>{const id=el.dataset.cell;
    rows.push([id,(t[id]||{}).consistency_score||'',(t[id]||{}).notes||''])});
  const blob=new Blob(['\\ufeff'+rows.map(r=>r.map(q).join(',')).join('\\n')],{type:'text/csv'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='scores.csv';a.click();
});
count();
"""


def _cell_html(r: dict[str, Any] | None) -> str:
    if r is None:
        return "<td class='cell skip'>（無）</td>"
    cid = html.escape(r["cell_id"])
    parts = [f"<td class='cell'><div class='hint'>seed {html.escape(str(r['seed']))}</div>"]
    if r.get("status") == "ok":
        proc, raw = html.escape(r.get("processed_image", "")), html.escape(r.get("raw_image", ""))
        parts.append(f"<img loading='lazy' src='{proc}' data-alt='{raw}' title='點擊切換原圖/線稿'>")
        parts.append(
            "<div class='m'>"
            f"耗時 {r.get('elapsed_s')}s<br>灰階 {float(r.get('gray_pixel_ratio') or 0):.3f} · "
            f"碎片 {r.get('fragment_count')}<br>封閉區 {r.get('closed_region_count')} · "
            f"過小 {r.get('tiny_region_count')}<br>黑填充 {float(r.get('black_fill_ratio') or 0):.3f}</div>")
    elif r.get("status") == "skipped":
        parts.append(f"<div class='skip'>略過：{html.escape(r.get('error', ''))}</div>")
    else:
        parts.append(f"<div class='err'>失敗：{html.escape(r.get('error', ''))}</div>")
    parts.append(
        f"<label>一致性 (1–5) <input type='number' min='1' max='5' step='1' data-cell='{cid}' "
        f"data-field='consistency_score'></label><br>"
        f"<textarea placeholder='備註' data-cell='{cid}' data-field='notes'></textarea></td>")
    return "".join(parts)


def write_report(run_dir: Path, run_name: str, rows: list[dict[str, Any]], cfg: MatrixConfig | None = None) -> Path:
    by_id = {r["cell_id"]: r for r in rows}
    scenes = list(dict.fromkeys(r["scene"] for r in rows))
    combos = list(dict.fromkeys((r["route"], r["lineart"]) for r in rows))
    seeds = list(dict.fromkeys(str(r["seed"]) for r in rows))

    body = [f"<h1>{html.escape(run_name)}</h1>"]
    ref = (cfg.character.get("reference_image") if cfg else None)
    if ref:
        ref_path = _resolve_local(ref)
        try:
            rel = Path(*([".."] * len(run_dir.resolve().relative_to(PROJECT_ROOT).parts))) / ref_path.relative_to(PROJECT_ROOT)
            src = rel.as_posix()
        except ValueError:
            src = ref_path.as_uri()
        body.append(f"<div class='ref'>角色參考圖：<br><img src='{html.escape(src)}'></div>")
    body.append(
        "<div class='bar'><button id='export'>匯出分數 CSV</button><span id='cnt'></span>"
        "<span class='hint'>分數暫存在瀏覽器（localStorage）；完成後匯出 scores.csv 放到本資料夾，"
        "再執行 run_matrix.py summarize。點圖可切換原始輸出／後處理線稿。</span></div>")
    for sc in scenes:
        body.append(f"<h2>場景：{html.escape(sc)}</h2><table><tr><th>路線 × 線稿方式</th>")
        body.extend(f"<th>seed {html.escape(s)}</th>" for s in seeds)
        body.append("</tr>")
        for route, la in combos:
            body.append(f"<tr><th>{html.escape(route)}<br>{html.escape(la)}</th>")
            for s in seeds:
                body.append(_cell_html(by_id.get(f"{sc}__{route}__{la}__s{s}")))
            body.append("</tr>")
        body.append("</table>")

    page = (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(run_name)}</title><style>{_REPORT_CSS}</style></head>"
            f"<body data-run='{html.escape(run_dir.name)}'>{''.join(body)}<script>{_REPORT_JS}</script></body></html>")
    out = run_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out


def summarize_run(run_dir: Path, scores_path: Path | None) -> int:
    rows = _read_rows(run_dir / "results.csv")
    if not rows:
        print(f"錯誤：{run_dir / 'results.csv'} 不存在或是空的", file=sys.stderr)
        return 2
    scores_path = scores_path or (run_dir / "scores.csv")
    scores = load_scores(scores_path) if scores_path.is_file() else None
    summary = summarize(rows, scores=scores)
    write_csv(summary, run_dir / "summary.csv")
    md = f"# {run_dir.name}\n\n" + summary_markdown(summary)
    if scores is None:
        md += f"\n（找不到 {scores_path.name}，尚未納入一致性分數）\n"
    (run_dir / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


# ---- 對既有結果重新擷取（Route C0）------------------------------------------------------

async def _reextract(src_dir: Path, raw_cfg: dict[str, Any], methods: dict[str, dict[str, Any]],
                     config_path: Path) -> int:
    src_rows = [r for r in _read_rows(src_dir / "results.csv")
                if r.get("status") == "ok" and r.get("raw_image") and not r.get("gen_source")]  # 共用的原圖只處理一次
    if not src_rows:
        print(f"錯誤：{src_dir / 'results.csv'} 沒有成功的格子", file=sys.stderr)
        return 2
    src_meta_path = src_dir / "run.json"
    src_meta = json.loads(src_meta_path.read_text(encoding="utf-8")) if src_meta_path.is_file() else {}
    character = raw_cfg.get("character") or (src_meta.get("config") or {}).get("character") or {}

    name = str(raw_cfg.get("name") or config_path.stem)
    route = str(raw_cfg.get("route") or "C0")
    out_root = Path(raw_cfg["output_dir"]) if raw_cfg.get("output_dir") else PROJECT_ROOT / "experiments" / "results"
    out_root = out_root if out_root.is_absolute() else PROJECT_ROOT / out_root
    run_dir = out_root / f"{name}_{datetime.now():%Y%m%d_%H%M%S}"
    (run_dir / "raw").mkdir(parents=True)
    meta = {"name": name, "config_file": str(config_path), "config": raw_cfg, "source_run": str(src_dir),
            "character": character, "started_at": datetime.now().isoformat(timespec="seconds"),
            "workflows": src_meta.get("workflows", {})}
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    keep = ("scene", "seed", "workflow", "workflow_version", "workflow_hash", "models", "elapsed_s",
            "prompt_id", "params_json")
    rows: list[dict[str, Any]] = []
    total = len(src_rows) * len(methods)
    for src in src_rows:
        raw_rel = f"raw/{src['cell_id']}.png"  # 複製一份，讓結果資料夾自成一體
        (run_dir / raw_rel).write_bytes((src_dir / src["raw_image"]).read_bytes())
        for m_key, spec in methods.items():
            lineart = f"{src['lineart']}-{m_key}"
            cell_id = f"{src['scene']}__{route}__{lineart}__s{src['seed']}"
            row: dict[str, Any] = {k: src.get(k, "") for k in keep}
            row.update(cell_id=cell_id, route=route, lineart=lineart, status="error", error="",
                       started_at=datetime.now().isoformat(timespec="seconds"),
                       gen_source=f"{src_dir.name}/{src['cell_id']}", raw_image=raw_rel)
            try:
                await postprocess_cell(run_dir / raw_rel, cell_id, spec, raw_cfg.get("postprocess") or {},
                                       run_dir, row)
                row["status"] = "ok"
            except (ValueError, OSError) as e:
                row["error"] = f"{type(e).__name__}: {e}"
            rows.append(row)
            print(f"[{len(rows)}/{total}] {cell_id} -> {row['status']}  closed_regions={row.get('closed_region_count')}")
    write_csv(rows, run_dir / "results.csv", RESULT_FIELDS)
    write_report(run_dir, name, rows, SimpleNamespace(character=character))
    n_ok = sum(r["status"] == "ok" for r in rows)
    print(f"\n完成：{n_ok}/{len(rows)} 格成功。\n結果：{run_dir / 'results.csv'}\n對照頁：{run_dir / 'report.html'}")
    return 0 if n_ok == len(rows) else 1


def reextract_run(src_dir: Path, config_path: Path) -> int:
    """對既有實驗結果的原始輸出套用多種擷取方式，輸出新的結果資料夾（不連線 ComfyUI）。

    設定檔：name、route（預設 C0）、output_dir、postprocess、character（選填，預設沿用來源）、
    extract_methods: {鍵: {method: ..., 參數...}}。每格的 lineart = <來源線稿方式>-<鍵>。
    """
    try:
        raw_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        methods = _require_mapping(raw_cfg.get("extract_methods"), "extract_methods")
        for k, spec in methods.items():
            try:
                ExtractSpec.from_dict(spec)
            except (ValueError, TypeError) as e:
                raise ConfigError(f"extract_methods.{k}：{e}") from e
        LineartParams.from_dict(raw_cfg.get("postprocess"))
    except (ConfigError, ValueError, OSError, yaml.YAMLError) as e:
        print(f"設定檔錯誤：{e}", file=sys.stderr)
        return 2
    return asyncio.run(_reextract(src_dir.resolve(), raw_cfg, methods, config_path))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="執行實驗矩陣")
    r.add_argument("config", type=Path)
    r.add_argument("--settings", type=Path)
    r.add_argument("--dry-run", action="store_true", help="只展開矩陣並驗證參數，不連線 ComfyUI")
    r.add_argument("--limit", type=int, help="只跑前 N 格")
    r.add_argument("--route", action="append", help="只跑指定路線（可重複）")
    r.add_argument("--resume", type=Path, help="接續既有的結果資料夾，跳過已成功的格子")
    rp = sub.add_parser("report", help="由 results.csv 重新產生 report.html")
    rp.add_argument("run_dir", type=Path)
    rp.add_argument("--config", type=Path)
    rx = sub.add_parser("reextract", help="對既有結果的原始輸出重新擷取線稿（不連線 ComfyUI）")
    rx.add_argument("run_dir", type=Path)
    rx.add_argument("--config", type=Path, required=True)
    s = sub.add_parser("summarize", help="彙總指標與一致性分數")
    s.add_argument("run_dir", type=Path)
    s.add_argument("--scores", type=Path)
    args = p.parse_args(argv)

    if args.cmd == "run":
        try:
            cfg = load_config(args.config)
        except (ConfigError, OSError, yaml.YAMLError) as e:
            print(f"設定檔錯誤：{e}", file=sys.stderr)
            return 2
        return asyncio.run(run_matrix(cfg, args.config, settings_path=args.settings, dry_run=args.dry_run,
                                      limit=args.limit, resume=args.resume, only_routes=args.route))
    if args.cmd == "reextract":
        return reextract_run(args.run_dir, args.config)
    if args.cmd == "report":
        rows = _read_rows(args.run_dir / "results.csv")
        cfg = load_config(args.config) if args.config else None
        if cfg is None and (args.run_dir / "run.json").is_file():
            meta = json.loads((args.run_dir / "run.json").read_text(encoding="utf-8"))
            cfg_path = Path(meta.get("config_file", ""))
            if meta.get("source_run"):  # reextract 的結果
                cfg = SimpleNamespace(character=meta.get("character") or {})
            elif cfg_path.is_file():
                cfg = load_config(cfg_path)
        print(write_report(args.run_dir.resolve(), args.run_dir.name, rows, cfg))
        return 0
    return summarize_run(args.run_dir, args.scores)


if __name__ == "__main__":
    sys.exit(main())
