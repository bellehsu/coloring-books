"""Task 6：背景提示詞校正（只寫文字的背景），用新的 sdxl_background workflow。"""
import asyncio, csv, io, json, sys, time
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, "/ai-data/repos/coloring-books/backend")
from app.config import load_settings
from app.models import GenKind
from app.services.comfy_client import ComfyClient
from app.services.comfy_engine import ComfyEngine
from app.services.engine import GenerationRequest
from app.services.workflow_registry import WorkflowRegistry
from app.services import prompts as P
from app.services.lineart_render import render_base, LineDetail
from app.services.qa import evaluate

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
STYLE = P.BACKGROUND_STYLE
SIMPLE_NEG = "cluttered, busy, intricate details, many small objects, tiny details, hatching, crosshatching, pattern, "
VARIANTS = {
  "A_current": (lambda s: P.background_prompt(s), P.BACKGROUND_NEGATIVE),
  "B_simple": (lambda s: f"{s}, simple coloring book background, a few large simple objects, lots of empty space, {STYLE}, no people", SIMPLE_NEG + P.BACKGROUND_NEGATIVE),
  "C_weighted": (lambda s: f"{s}, (simple coloring book background:1.2), (a few large simple objects:1.3), lots of empty space, {STYLE}, no people", SIMPLE_NEG + P.BACKGROUND_NEGATIVE),
}
SCENES = {"park": "a sunny park playground with a swing set, trees and flowers",
          "kitchen": "a cozy kitchen with cupboards, an oven, a table with a bowl and cookies",
          "bedroom": "a cozy bedroom with a bed, a bookshelf and a window"}
SEEDS = [101, 202]

async def main():
    s = load_settings(); reg = WorkflowRegistry(s.paths.workflows_dir).load()
    eng = ComfyEngine(client_factory=lambda: ComfyClient(s.comfy.url, request_timeout_s=60), registry=reg, run_timeout_s=600)
    rows = []
    for v, (pf, neg) in VARIANTS.items():
        for k, scene in SCENES.items():
            for seed in SEEDS:
                t = time.time()
                r = await eng.generate(GenerationRequest(kind=GenKind.BACKGROUND, prompt=pf(scene), negative_prompt=neg, seed=seed, width=864, height=1216))
                f = OUT / f"{v}__{k}__s{seed}.png"; f.write_bytes(r.image)
                img = np.asarray(Image.open(io.BytesIO(r.image)).convert("RGB"))
                ink, m = render_base(img, (2244, 3272), LineDetail.STANDARD, 11)
                qa = evaluate(ink, 11)
                rows.append({"variant": v, "scene": k, "seed": seed, "gaps": qa.gap_count, "dangling": qa.dangling_count,
                             "regions": qa.metrics.get("region_count"), "tiny": qa.metrics.get("tiny_region_count"),
                             "ink_ratio": round(float(ink.mean()), 4), "elapsed_s": round(time.time() - t, 1), "prompt": pf(scene)})
                print(v, k, seed, rows[-1]["gaps"], rows[-1]["tiny"], rows[-1]["ink_ratio"], flush=True)
    with (OUT / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, rows[0].keys()); w.writeheader(); w.writerows(rows)
asyncio.run(main())
