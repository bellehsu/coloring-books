#!/usr/bin/env python3
"""依 config/settings.yaml 的 comfy.models_root 產生 ComfyUI 的 extra_model_paths.yaml。

用法：
  python scripts/sync_comfy_model_paths.py --dry-run                 # 顯示將寫入 install_dir 的內容與差異
  python scripts/sync_comfy_model_paths.py --install-dir              # 寫入 comfy.install_dir/extra_model_paths.yaml
  python scripts/sync_comfy_model_paths.py --output path/to/x.yaml    # 寫入指定路徑
  python scripts/sync_comfy_model_paths.py --output x.yaml --dry-run  # 預覽寫入指定路徑

寫入既有檔案時只取代本專案的區段（預設 key：coloring_books），其他區段原樣保留，
並先備份成 <檔名>.bak。修改 models_root 後需重啟 ComfyUI 才會生效。
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import SettingsError, load_settings  # noqa: E402

SECTION_KEY = "coloring_books"

# ComfyUI 的資料夾類型 → models_root 底下的子資料夾名稱。
# 目前 workflow 只用 unet / text_encoders / vae / loras / background_removal；
# SDXL 時期的類型（checkpoints、controlnet、ipadapter、clip_vision）保留，
# 讓 archive/ 的舊 workflow 與既有模型仍可被 ComfyUI 找到（多列的資料夾不影響運作）。
MODEL_SUBFOLDERS: dict[str, str] = {
    "checkpoints": "checkpoints",
    "loras": "loras",
    "controlnet": "controlnet",
    "ipadapter": "ipadapter",  # ComfyUI_IPAdapter_plus 註冊的類型
    "clip_vision": "clip_vision",
    "unet": "unet",  # ComfyUI-GGUF 的 UnetLoaderGGUF 也會掃這裡
    "diffusion_models": "diffusion_models",
    "text_encoders": "text_encoders",
    "clip": "clip",
    "vae": "vae",
    "background_removal": "background_removal",  # 內建 LoadBackgroundRemovalModel（BiRefNet）
    "upscale_models": "upscale_models",
    "embeddings": "embeddings",
}


def build_section(models_root: Path) -> dict:
    section: dict = {"base_path": str(models_root)}
    section.update(MODEL_SUBFOLDERS)
    return section


def merge_into(existing_text: str | None, section_key: str, section: dict) -> tuple[str, list[str]]:
    """把本專案區段併入既有 YAML 內容。回傳 (新內容, 警告訊息)。"""
    warnings: list[str] = []
    data: dict = {}
    if existing_text:
        loaded = yaml.safe_load(existing_text)
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("既有 extra_model_paths.yaml 頂層不是 mapping，拒絕覆寫")
        data = loaded
    for key, other in data.items():
        if key == section_key or not isinstance(other, dict):
            continue
        if str(other.get("base_path", "")).rstrip("/") == section["base_path"].rstrip("/"):
            warnings.append(
                f"區段 '{key}' 也指向同一個 base_path（{section['base_path']}），"
                f"兩者並存不影響運作，但可考慮移除其中一個。"
            )
    data[section_key] = section
    header = (
        "# 由 coloring-books/scripts/sync_comfy_model_paths.py 維護 "
        f"'{section_key}' 區段；其他區段的內容保留，但註解與格式會被重寫。\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), warnings


def missing_subfolders(models_root: Path) -> list[Path]:
    return [models_root / sub for sub in dict.fromkeys(MODEL_SUBFOLDERS.values()) if not (models_root / sub).is_dir()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--install-dir", action="store_true", help="寫入 comfy.install_dir/extra_model_paths.yaml")
    mode.add_argument("--output", type=Path, help="寫入指定路徑")
    p.add_argument("--dry-run", action="store_true",
                   help="只顯示將寫入的內容與差異，不寫檔（未指定目標時以 install_dir 為目標）")
    p.add_argument("--settings", type=Path, help="設定檔路徑（預設 config/settings.yaml 或 APP_SETTINGS_FILE）")
    p.add_argument("--section", default=SECTION_KEY, help=f"YAML 區段名稱（預設 {SECTION_KEY}）")
    p.add_argument("--create-dirs", action="store_true", help="在 models_root 底下建立缺少的子資料夾")
    args = p.parse_args(argv)

    try:
        # install_dir 模式才要求 install_dir 存在；其餘只需 models_root
        settings = load_settings(args.settings, check_paths=False)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2

    models_root = settings.comfy.models_root
    if models_root is None:
        print("錯誤：settings 未設定 comfy.models_root（此腳本需要它；可寫進設定檔或設環境變數 APP_COMFY__MODELS_ROOT）",
              file=sys.stderr)
        return 2
    if not models_root.is_dir():
        print(f"錯誤：comfy.models_root 不存在：{models_root}", file=sys.stderr)
        return 2

    if not (args.install_dir or args.output or args.dry_run):
        p.error("請指定 --dry-run、--install-dir 或 --output")

    if args.output:
        target = args.output.expanduser().resolve()
    else:
        install_dir = settings.comfy.install_dir
        if install_dir is None:
            if not args.dry_run:
                print("錯誤：settings 未設定 comfy.install_dir，請改用 --output 指定路徑", file=sys.stderr)
                return 2
            target = None
        elif not (install_dir / "main.py").is_file():
            print(f"錯誤：{install_dir} 不是 ComfyUI 安裝目錄（缺 main.py）", file=sys.stderr)
            return 2
        else:
            target = install_dir / "extra_model_paths.yaml"

    existing = target.read_text(encoding="utf-8") if target and target.is_file() else None
    try:
        new_text, warnings = merge_into(existing, args.section, build_section(models_root))
    except (ValueError, yaml.YAMLError) as e:
        print(f"錯誤：{target}: {e}", file=sys.stderr)
        return 2

    for w in warnings:
        print(f"注意：{w}", file=sys.stderr)
    missing = missing_subfolders(models_root)

    if args.dry_run:
        print(f"# 目標檔案：{target or '（未設定 install_dir；請用 --output 指定）'}")
        if existing is not None:
            diff = difflib.unified_diff(
                existing.splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=f"{target}（目前）", tofile=f"{target}（寫入後）",
            )
            sys.stdout.writelines(diff)
        else:
            sys.stdout.write(new_text)
        if missing:
            print("\n# models_root 底下缺少的子資料夾（可加 --create-dirs 建立）：")
            for m in missing:
                print(f"#   {m}")
        return 0

    if args.create_dirs:
        for m in missing:
            m.mkdir(parents=True, exist_ok=True)
            print(f"已建立 {m}")
    elif missing:
        print("注意：models_root 底下缺少子資料夾（加 --create-dirs 建立）：" + ", ".join(m.name for m in missing),
              file=sys.stderr)

    target.parent.mkdir(parents=True, exist_ok=True)
    if existing is not None:
        backup = target.with_name(target.name + ".bak")
        if backup.exists():
            print(f"{backup} 已存在，不覆寫（保留第一次備份的原檔）")
        else:
            shutil.copy2(target, backup)
            print(f"已備份原檔到 {backup}")
    target.write_text(new_text, encoding="utf-8")
    print(f"已寫入 {target}")
    print("請重啟 ComfyUI 讓新路徑生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
