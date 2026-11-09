#!/usr/bin/env python3
"""把後端的 OpenAPI 結構輸出成 JSON，供前端產生型別。不啟動 lifespan（不連 ComfyUI、不建資料庫）。

用法：
  python scripts/export_openapi.py [out_path]
預設輸出 frontend/src/api/openapi.json（相對於專案根目錄）。
設定檔沿用 load_settings()：config/settings.yaml，或 APP_SETTINGS_FILE 指定的檔案。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import SettingsError, load_settings  # noqa: E402
from app.main import create_app  # noqa: E402

DEFAULT_OUT = ROOT / "frontend" / "src" / "api" / "openapi.json"


def export_openapi(out_path: Path) -> Path:
    app = create_app(load_settings(check_paths=False), start_worker=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out_path", nargs="?", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    try:
        out = export_openapi(args.out_path)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"已輸出 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
