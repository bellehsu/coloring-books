"""實驗用的線稿指標與彙總。

單張圖指標的定義與實作在 app.services.lineart_post（後端與實驗共用同一份），
這裡負責：
  - 從檔案計算指標（已處理的 1-bit PNG 或任何圖片）
  - 依「路線 × 線稿方式」等欄位彙總多張結果（平均、標準差、成功率、耗時、一致性分數）
  - 讀取對照頁匯出的一致性分數 CSV
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from app.services.lineart_post import LineartParams, compute_metrics, load_image, to_gray

METRIC_KEYS = (
    "gray_pixel_ratio",
    "fragment_count",
    "black_fill_ratio",
    "region_count",
    "closed_region_count",
    "tiny_region_count",
    "black_pixel_ratio",
)


def image_metrics(path: str | Path, params: LineartParams | dict | None = None,
                  *, threshold: int = 128) -> dict[str, float | int]:
    """對單一圖檔直接計算指標（不做後處理）。灰階 < threshold 視為黑。"""
    p = params if isinstance(params, LineartParams) else LineartParams.from_dict(params)
    gray = to_gray(load_image(path))
    ink = gray < threshold
    return compute_metrics(ink, p, source_gray=gray)


def load_scores(path: str | Path) -> dict[str, dict[str, str]]:
    """讀取對照頁匯出的分數 CSV（欄位：cell_id, consistency_score, notes）。"""
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = (row.get("cell_id") or "").strip()
            if cid:
                out[cid] = row
    return out


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _mean_std(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    m = sum(values) / len(values)
    if len(values) == 1:
        return m, 0.0
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return m, math.sqrt(var)


def summarize(rows: Iterable[dict[str, Any]], group_by: Sequence[str] = ("route", "lineart"),
              scores: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """依 group_by 彙總。rows 為 run_matrix 輸出的結果列（status == "ok" 才計入指標）。"""
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(r.get(k, "") for k in group_by)].append(r)

    summary: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        ok = [r for r in items if r.get("status") == "ok"]
        s: dict[str, Any] = dict(zip(group_by, key))
        s["n"] = len(items)
        s["n_ok"] = len(ok)
        s["success_rate"] = round(len(ok) / len(items), 3) if items else 0.0
        for k in (*METRIC_KEYS, "elapsed_s"):
            vals = [v for v in (_to_float(r.get(k)) for r in ok) if v is not None]
            m, sd = _mean_std(vals)
            s[f"{k}_mean"] = None if m is None else round(m, 4)
            s[f"{k}_std"] = None if sd is None else round(sd, 4)
        if scores is not None:
            vals = [v for v in (_to_float((scores.get(r["cell_id"]) or {}).get("consistency_score")) for r in items)
                    if v is not None]
            m, sd = _mean_std(vals)
            s["consistency_n"] = len(vals)
            s["consistency_mean"] = None if m is None else round(m, 3)
            s["consistency_std"] = None if sd is None else round(sd, 3)
        failures = [r.get("error", "") for r in items if r.get("status") != "ok"]
        s["failures"] = " | ".join(sorted(set(f for f in failures if f)))[:500]
        summary.append(s)
    return summary


def write_csv(rows: Sequence[dict[str, Any]], path: str | Path, fieldnames: Sequence[str] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})
    return path


def summary_markdown(summary: Sequence[dict[str, Any]], group_by: Sequence[str] = ("route", "lineart")) -> str:
    cols = [*group_by, "n_ok/n", "consistency", "elapsed_s", "gray_pixel_ratio", "fragment_count",
            "closed_region_count", "tiny_region_count", "black_fill_ratio"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]

    def fmt(s: dict, k: str) -> str:
        m, sd = s.get(f"{k}_mean"), s.get(f"{k}_std")
        return "-" if m is None else f"{m:g} ± {sd:g}"

    for s in summary:
        cells = [str(s[k]) for k in group_by]
        cells.append(f"{s['n_ok']}/{s['n']}")
        cells.append("-" if s.get("consistency_mean") is None
                     else f"{s['consistency_mean']:g} ± {s['consistency_std']:g} (n={s['consistency_n']})")
        cells += [fmt(s, k) for k in ("elapsed_s", "gray_pixel_ratio", "fragment_count", "closed_region_count",
                                      "tiny_region_count", "black_fill_ratio")]
        lines.append("| " + " | ".join(cells) + " |")
    failures = [(s, s["failures"]) for s in summary if s.get("failures")]
    if failures:
        lines.append("\n失敗原因：")
        for s, f in failures:
            lines.append(f"- {' × '.join(str(s[k]) for k in group_by)}：{f}")
    return "\n".join(lines) + "\n"
