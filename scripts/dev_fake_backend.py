#!/usr/bin/env python3
"""不需要 GPU／ComfyUI 的假生成後端：給前端開發與 e2e 測試用。

真正的 FastAPI 後端（create_app）＋ DemoEngine：每張圖延遲 0.3 秒、回報 3 次進度，
用 Pillow 畫一張彩色圖（白底、黑色粗外框的角色與幾個背景方塊）。其中一個不填色的大框
刻意留了一個缺口，讓 QA 偵測到「線條有缺口」、自動補線後缺口歸零。

用法：
  python scripts/dev_fake_backend.py [--port 8000] [--host 127.0.0.1] [--storage <dir>]

設定沿用 load_settings()（config/settings.yaml 或 APP_SETTINGS_FILE，可被 APP_ 環境變數覆寫），
但 paths.storage_dir／paths.database 一律改到 --storage（預設：新的暫存資料夾），不會碰到正式資料。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image, ImageDraw  # noqa: E402

from app.config import Settings, SettingsError, load_settings  # noqa: E402
from app.models import GenKind  # noqa: E402
from app.services.engine import GenerationRequest, GenerationResult, ProgressFn  # noqa: E402

OUTLINE_PX = 6
# 缺口寬度（生成圖的像素）。線稿會放大到 300 DPI 內容尺寸再處理：A4 約放大 3 倍，
# 7 px ≈ 線稿上 20 px，QA 判定為會漏色的缺口（骨架距離 < max_gap_px），自動補線補得起來。
# 太寬（例如生成圖上 20 px ≈ 線稿 60 px）QA 會當成刻意留白，不算缺口。
GAP_PX = 7
# 缺口所在的大框（相對座標 x0, y0, x1, y1）；缺口在上緣中央。
GAP_FRAME = (0.08, 0.70, 0.92, 0.94)

_PALETTE = [
    ((255, 214, 170), (190, 225, 255), (250, 200, 210), (255, 236, 200), (170, 220, 170)),
    ((255, 240, 160), (210, 200, 250), (255, 190, 170), (250, 230, 210), (160, 210, 230)),
    ((200, 240, 200), (255, 210, 230), (240, 210, 180), (255, 245, 215), (230, 190, 240)),
    ((190, 230, 240), (255, 225, 180), (220, 200, 200), (250, 235, 205), (250, 200, 160)),
]


def draw_demo(width: int, height: int, seed: int = 0) -> Image.Image:
    """白底、黑色粗外框（OUTLINE_PX）的角色（橢圓耳朵、圓頭、方身體）與背景方塊；GAP_FRAME 留 GAP_PX 缺口。

    seed 只改變填色與背景方塊的位置（候選圖看得出差異），缺口的位置與寬度固定。
    """
    im = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(im)
    lw = OUTLINE_PX
    box_a, box_b, ear, head, body = _PALETTE[seed % len(_PALETTE)]
    shift = (seed % 3) * 0.02

    def r(x0: float, y0: float, x1: float, y1: float) -> list[float]:
        return [x0 * width, y0 * height, x1 * width, y1 * height]

    # 背景方塊（填色）
    d.rectangle(r(0.06, 0.06 + shift, 0.30, 0.20 + shift), fill=box_a, outline="black", width=lw)
    d.rectangle(r(0.70, 0.08, 0.94, 0.24 - shift), fill=box_b, outline="black", width=lw)
    # 不填色的大框，上緣中央挖一個缺口
    x0, y0, x1, y1 = r(*GAP_FRAME)
    d.rectangle([x0, y0, x1, y1], outline="black", width=lw)
    cx = round((x0 + x1) / 2)
    d.rectangle([cx - GAP_PX // 2, y0 - lw, cx - GAP_PX // 2 + GAP_PX - 1, y0 + lw], fill="white")
    # 角色：兩隻橢圓耳朵、圓頭、方身體
    d.ellipse(r(0.30, 0.22, 0.42, 0.34), fill=ear, outline="black", width=lw)
    d.ellipse(r(0.58, 0.22, 0.70, 0.34), fill=ear, outline="black", width=lw)
    d.ellipse(r(0.32, 0.27, 0.68, 0.50), fill=head, outline="black", width=lw)
    d.rectangle(r(0.36, 0.50, 0.64, 0.66), fill=body, outline="black", width=lw)
    return im


class DemoEngine:
    """GenerationEngine 的假實作：不連 ComfyUI，畫一張 draw_demo 圖。"""

    def __init__(self, delay_s: float = 0.3, progress_steps: int = 3) -> None:
        self.delay_s = delay_s
        self.progress_steps = progress_steps

    async def generate(self, req: GenerationRequest, on_progress: ProgressFn | None = None) -> GenerationResult:
        start = time.monotonic()
        n = max(1, self.progress_steps)
        for i in range(1, n + 1):
            await asyncio.sleep(self.delay_s / n)
            if on_progress:
                on_progress(i, n)
        buf = io.BytesIO()
        draw_demo(req.width, req.height, req.seed).save(buf, "PNG")
        return GenerationResult(
            image=buf.getvalue(),
            elapsed_s=round(time.monotonic() - start, 3),
            engine="demo",
            workflow_name="demo",
            workflow_version="demo",
            workflow_hash="demo",
            models=[],
            params={
                "kind": GenKind(req.kind).value,
                "seed": req.seed,
                "width": req.width,
                "height": req.height,
                "has_character_image": req.character_image is not None,
                "has_structure_image": req.structure_image is not None,
                "structure_strength": req.structure_strength,
            },
        )


def demo_settings(storage: Path, base: Settings | None = None) -> Settings:
    """load_settings() 的設定，但 storage_dir／database 改到 storage（會建立資料夾）。"""
    s = base or load_settings(check_paths=False)
    storage = storage.expanduser().resolve()
    storage.mkdir(parents=True, exist_ok=True)
    return s.model_copy(update={
        "paths": s.paths.model_copy(update={"storage_dir": storage, "database": storage / "app.db"}),
    })


def create_demo_app(storage: Path, base: Settings | None = None, engine: DemoEngine | None = None):
    from app.main import create_app

    return create_app(demo_settings(storage, base), engine=engine or DemoEngine(), start_worker=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--storage", type=Path, default=None, help="資料夾（含 SQLite）；預設新建暫存資料夾")
    args = p.parse_args(argv)
    storage = args.storage or Path(tempfile.mkdtemp(prefix="coloring-books-fake-"))
    try:
        app = create_demo_app(storage)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"假後端：http://{args.host}:{args.port}  storage={storage}", flush=True)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
