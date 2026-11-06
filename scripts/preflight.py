#!/usr/bin/env python3
"""命令列版 preflight：檢查每個 workflow 需要的節點、模型與輸入欄位是否存在於執行中的 ComfyUI。

用法：
  python scripts/preflight.py [--settings PATH] [--comfy-url URL] [--workflow NAME] [--json]
結束碼：0 全部通過；1 有 workflow 未通過；2 設定錯誤。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import SettingsError, load_settings  # noqa: E402
from app.services.comfy_client import ComfyClient  # noqa: E402
from app.services.preflight import format_report, run_preflight  # noqa: E402
from app.services.workflow_registry import WorkflowRegistry  # noqa: E402


async def _main(args: argparse.Namespace) -> int:
    try:
        settings = load_settings(args.settings)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    registry = WorkflowRegistry(settings.paths.workflows_dir).load()
    if args.workflow:
        registry.entries = {k: v for k, v in registry.entries.items() if k in args.workflow}
    url = args.comfy_url or settings.comfy.url
    async with ComfyClient(url, request_timeout_s=settings.comfy.request_timeout_s) as client:
        report = await run_preflight(client, registry)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0 if report.ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--settings", type=Path)
    p.add_argument("--comfy-url", help="覆寫 settings 的 comfy.url")
    p.add_argument("--workflow", action="append", help="只檢查指定 workflow（可重複）")
    p.add_argument("--json", action="store_true")
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
