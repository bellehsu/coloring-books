#!/usr/bin/env python3
"""不需要 GPU／ComfyUI 的假生成後端：給前端開發與 e2e 測試用。

真正的 FastAPI 後端（create_app）＋ DemoEngine：每張圖延遲 0.3 秒、回報 3 次進度，用 Pillow 畫彩色圖：
- character／page（舊流程）：白底、黑色粗外框的角色與幾個背景方塊（draw_demo）。
- background：幾個大物件（太陽、雲、樹、草叢）＋上方一個不填色的大框（draw_background）。
- pose：白底上的同一個角色，固定 832×1216，另附遮罩（角色處白、其他黑；draw_pose）。
不填色的大框刻意留了一個缺口，讓 QA 偵測到「線條有缺口」、自動補線後缺口歸零。
背景的缺口框在畫面上方，預設位置（DEFAULT_PLACEMENT）的角色不會蓋到它。

用法：
  python scripts/dev_fake_backend.py [--port 8000] [--host 127.0.0.1] [--storage <dir>]

設定沿用 load_settings()（config/settings.yaml 或 APP_SETTINGS_FILE，可被 APP_ 環境變數覆寫），
但 paths.storage_dir／paths.database 一律改到 --storage（預設：新的暫存資料夾），不會碰到正式資料。
啟動時不跑 ComfyUI preflight（快取維持 None），
結果不受本機 ComfyUI 有沒有開、workflow 能不能跑影響（e2e 才穩定）。
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
from app.services.prompts import POSE_SIZE  # noqa: E402

OUTLINE_PX = 6
# 缺口寬度（生成圖的像素）。線稿會放大到 300 DPI 內容尺寸再處理：A4 約放大 3 倍，
# 7 px ≈ 線稿上 20 px，QA 判定為會漏色的缺口（骨架距離 < max_gap_px），自動補線補得起來。
# 太寬（例如生成圖上 20 px ≈ 線稿 60 px）QA 會當成刻意留白，不算缺口。
GAP_PX = 7
# 缺口所在的大框（相對座標 x0, y0, x1, y1）；缺口在上緣中央。
GAP_FRAME = (0.08, 0.70, 0.92, 0.94)
# 背景的缺口框：畫面上方（預設位置的角色在 y 0.36–0.96，不會蓋到），缺口在上緣中央。
BG_GAP_FRAME = (0.08, 0.20, 0.92, 0.32)

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
    _gap_box(d, r(*GAP_FRAME))
    _draw_character(d, r, (ear, head, body))
    return im


# 角色的各部位（相對座標）：兩隻橢圓耳朵、圓頭、方身體
_CHARACTER = (
    ("ellipse", (0.30, 0.22, 0.42, 0.34)),
    ("ellipse", (0.58, 0.22, 0.70, 0.34)),
    ("ellipse", (0.32, 0.27, 0.68, 0.50)),
    ("rectangle", (0.36, 0.50, 0.64, 0.66)),
)


def _draw_character(d: ImageDraw.ImageDraw, r, fills: tuple[tuple[int, int, int], ...] | None) -> None:
    """fills＝(耳朵, 頭, 身體) 的填色：畫黑色粗外框的角色；None：畫遮罩（同樣外形含外框整塊填 255）。"""
    for i, (shape, box) in enumerate(_CHARACTER):
        draw = getattr(d, shape)
        if fills is None:
            draw(r(*box), fill=255)
        else:
            draw(r(*box), fill=fills[(0, 0, 1, 2)[i]], outline="black", width=OUTLINE_PX)  # 兩隻耳朵同色


def _gap_box(d: ImageDraw.ImageDraw, box: list[float]) -> None:
    """不填色的封閉框，上緣中央挖 GAP_PX 寬的缺口。"""
    x0, y0, x1, y1 = box
    lw = OUTLINE_PX
    d.rectangle([x0, y0, x1, y1], outline="black", width=lw)
    cx = round((x0 + x1) / 2)
    d.rectangle([cx - GAP_PX // 2, y0 - lw, cx - GAP_PX // 2 + GAP_PX - 1, y0 + lw], fill="white")


def draw_background(width: int, height: int, seed: int = 0) -> Image.Image:
    """背景：太陽、雲、樹、草叢幾個填色的大物件，加上 BG_GAP_FRAME 的不填色大框（留 GAP_PX 缺口）。

    所有物件都避開角色的位置（DEFAULT_PLACEMENT 在中央下方，左右移動幾 % 也一樣）：缺口框在上方、
    樹在右側、草叢在左側。角色不碰到背景線條，合成後的線稿只有這一個缺口（角色線條緊貼背景線條時，
    兩條線之間的窄縫會被 QA 當成另一個缺口，結果就不確定）。seed 只改變填色，缺口的位置與寬度固定。
    """
    im = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(im)
    lw = OUTLINE_PX
    sun, cloud, crown, trunk, bush = _PALETTE[seed % len(_PALETTE)]

    def r(x0: float, y0: float, x1: float, y1: float) -> list[float]:
        return [x0 * width, y0 * height, x1 * width, y1 * height]

    d.ellipse(r(0.06, 0.03, 0.24, 0.15), fill=sun, outline="black", width=lw)
    d.rectangle(r(0.56, 0.05, 0.92, 0.14), fill=cloud, outline="black", width=lw)
    _gap_box(d, r(*BG_GAP_FRAME))
    d.rectangle(r(0.86, 0.56, 0.92, 0.86), fill=trunk, outline="black", width=lw)
    d.ellipse(r(0.80, 0.38, 0.98, 0.60), fill=crown, outline="black", width=lw)
    d.ellipse(r(0.02, 0.68, 0.18, 0.84), fill=bush, outline="black", width=lw)
    return im


def draw_pose(seed: int = 0) -> tuple[Image.Image, Image.Image]:
    """角色動作：白底上的角色（同 draw_demo 的角色），固定 POSE_SIZE；另回傳同尺寸遮罩（角色處 255、其他 0）。"""
    width, height = POSE_SIZE
    _, _, ear, head, body = _PALETTE[seed % len(_PALETTE)]

    def r(x0: float, y0: float, x1: float, y1: float) -> list[float]:
        return [x0 * width, y0 * height, x1 * width, y1 * height]

    im = Image.new("RGB", (width, height), "white")
    _draw_character(ImageDraw.Draw(im), r, (ear, head, body))
    mask = Image.new("L", (width, height), 0)
    _draw_character(ImageDraw.Draw(mask), r, None)
    return im, mask


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class DemoEngine:
    """GenerationEngine 的假實作：不連 ComfyUI。background → draw_background（請求的尺寸）；
    pose → draw_pose（固定 POSE_SIZE，extra_images 附 "mask"）；其他 → draw_demo。"""

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
        kind = GenKind(req.kind)
        extra: dict[str, bytes] = {}
        if kind == GenKind.BACKGROUND:
            image = draw_background(req.width, req.height, req.seed)
        elif kind == GenKind.POSE:
            image, mask = draw_pose(req.seed)
            extra["mask"] = _png(mask)
        else:
            image = draw_demo(req.width, req.height, req.seed)
        params = {
            "kind": kind.value,
            "seed": req.seed,
            "width": image.width,
            "height": image.height,
            "has_character_image": req.character_image is not None,
        }
        if req.variation:
            params["variation"] = req.variation
        return GenerationResult(
            image=_png(image),
            extra_images=extra,
            elapsed_s=round(time.monotonic() - start, 3),
            engine="demo",
            workflow_name="demo",
            workflow_version="demo",
            workflow_hash="demo",
            models=[],
            params=params,
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

    # 不跑啟動 preflight：DemoEngine 不連 ComfyUI，也不該看本機 ComfyUI 的狀態
    return create_app(demo_settings(storage, base), engine=engine or DemoEngine(), start_worker=True,
                      startup_preflight=False)


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
