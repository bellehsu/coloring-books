#!/usr/bin/env python3
"""環境檢查：Python、torch、CUDA、sm_120、VRAM、ComfyUI 連線。

只用標準函式庫（pyyaml 選用），可以用任何直譯器執行：
  - 專案 venv：檢查後端環境 + ComfyUI 連線（後端不需要 torch，torch 項目會顯示「未安裝」）
  - ComfyUI 的直譯器：檢查實際跑生成的 torch / CUDA
另外會從 ComfyUI 的 /system_stats 讀出「ComfyUI 那一側」的 torch 版本與 GPU，
所以只要 ComfyUI 在跑，用哪個直譯器執行都看得到真正跑生成的環境。

用法：
  python scripts/check_env.py [--comfy-url URL] [--settings PATH] [--json]
結束碼：0 全部通過；1 有失敗項目。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)
REQUIRED_ARCH = "sm_120"
MIN_CUDA = (12, 8)
MIN_VRAM_GB = 15.0  # RTX 5080 回報約 15.9 GiB


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, name: str, ok: bool | None, detail: str) -> None:
        """ok=None 代表「略過/資訊」，不算失敗。"""
        self.items.append({"name": name, "ok": ok, "detail": detail})

    @property
    def failed(self) -> bool:
        return any(i["ok"] is False for i in self.items)

    def print(self) -> None:
        mark = {True: "OK  ", False: "FAIL", None: "--  "}
        for i in self.items:
            print(f"[{mark[i['ok']]}] {i['name']}: {i['detail']}")


def _version_tuple(s: str | None) -> tuple[int, ...]:
    if not s:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", s)[:2])


def comfy_url_from_settings(settings_path: Path | None) -> str | None:
    env = os.environ.get("APP_COMFY__URL")
    if env:
        return env
    path = settings_path or Path(os.environ.get("APP_SETTINGS_FILE", ROOT / "config" / "settings.yaml"))
    if not path.is_file():
        return None
    try:
        import yaml  # type: ignore
    except ImportError:
        m = re.search(r"^\s*url:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (data.get("comfy") or {}).get("url")


def check_python(r: Report) -> None:
    v = sys.version_info
    r.add("Python", (v.major, v.minor) >= MIN_PYTHON,
          f"{platform.python_version()} ({sys.executable})；需要 >= {'.'.join(map(str, MIN_PYTHON))}")


def check_torch(r: Report) -> None:
    try:
        import torch  # type: ignore
    except ImportError:
        r.add("torch", None, f"此直譯器未安裝 torch（{sys.executable}）。後端不需要；GPU 檢查請看 ComfyUI 端結果")
        return
    cuda_ver = torch.version.cuda
    r.add("torch", True, f"{torch.__version__}（CUDA build {cuda_ver}）")
    r.add("torch CUDA 版本", _version_tuple(cuda_ver) >= MIN_CUDA,
          f"{cuda_ver}；需要 cu{MIN_CUDA[0]}{MIN_CUDA[1]} 以上")
    avail = torch.cuda.is_available()
    r.add("CUDA 可用", avail, str(avail))
    if not avail:
        return
    archs = torch.cuda.get_arch_list()
    r.add(f"支援 {REQUIRED_ARCH}", REQUIRED_ARCH in archs, ", ".join(archs))
    props = torch.cuda.get_device_properties(0)
    cap = f"sm_{props.major}{props.minor}"
    vram = props.total_memory / 1024**3
    r.add("GPU", True, f"{props.name}（{cap}）")
    r.add("VRAM", vram >= MIN_VRAM_GB, f"{vram:.1f} GiB；預期 >= {MIN_VRAM_GB} GiB")


def _get_json(url: str, timeout: float) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 本機設定的 URL
        return json.loads(resp.read().decode("utf-8"))


def check_comfy(r: Report, url: str | None, timeout: float) -> None:
    if not url:
        r.add("ComfyUI 連線", False, "找不到 comfy.url（config/settings.yaml 或 --comfy-url）")
        return
    try:
        stats = _get_json(url.rstrip("/") + "/system_stats", timeout)
    except urllib.error.HTTPError as e:
        r.add("ComfyUI 連線", False, f"{url}/system_stats 回傳 HTTP {e.code} —— 這個埠有服務在跑，但不是 ComfyUI")
        return
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        r.add("ComfyUI 連線", False, f"{url} 無法連線：{e}")
        return
    except json.JSONDecodeError:
        r.add("ComfyUI 連線", False, f"{url}/system_stats 回傳的不是 JSON —— 這個埠可能是別的服務")
        return
    if not isinstance(stats, dict) or "system" not in stats:
        r.add("ComfyUI 連線", False, f"{url}/system_stats 格式不符 —— 這個埠可能不是 ComfyUI")
        return
    system = stats["system"]
    r.add("ComfyUI 連線", True, f"{url}（ComfyUI {system.get('comfyui_version', '?')}）")
    tv = system.get("pytorch_version", "")
    m = re.search(r"\+cu(\d+)", tv)
    if m:
        digits = m.group(1)
        cu = (int(digits[:-1]), int(digits[-1]))
        r.add("ComfyUI torch", cu >= MIN_CUDA, f"{tv}；需要 cu{MIN_CUDA[0]}{MIN_CUDA[1]} 以上")
    else:
        r.add("ComfyUI torch", None, f"{tv}（無法判斷 CUDA 版本）")
    for dev in stats.get("devices", []):
        if dev.get("type") != "cuda":
            continue
        vram = dev.get("vram_total", 0) / 1024**3
        r.add("ComfyUI GPU", vram >= MIN_VRAM_GB, f"{dev.get('name')}；VRAM {vram:.1f} GiB")
        break
    else:
        r.add("ComfyUI GPU", False, "ComfyUI 沒有回報 CUDA 裝置（可能以 CPU 模式啟動）")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--comfy-url", help="覆寫 settings 的 comfy.url")
    p.add_argument("--settings", type=Path)
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--json", action="store_true", help="以 JSON 輸出")
    args = p.parse_args(argv)

    r = Report()
    check_python(r)
    check_torch(r)
    check_comfy(r, args.comfy_url or comfy_url_from_settings(args.settings), args.timeout)

    if args.json:
        print(json.dumps({"ok": not r.failed, "items": r.items}, ensure_ascii=False, indent=2))
    else:
        r.print()
        print("\n結果：" + ("全部通過" if not r.failed else "有項目失敗"))
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
