"""Phase 8 Task 3：qwen_background 的構圖變化語校正（「再產生一張」要真的不一樣）。

走正式 workflow（WorkflowRegistry → qwen_background）與 prompts.qwen_background_prompt。
6 場景（同 phase7 spike）× seed 101、202。每個版本：
- V0：不加變化語（基準，等同 spike QA1）。
- VA／VB／VC：候選變化語清單；s101 用清單第 i 個、s202 用第 i+1 個（模擬兩次隨機抽到不同的）。
指標：線稿缺口／過小區域（render_base → qa.evaluate，同 spike）、
同場景兩張的差異 diff（線稿縮到 96×136 後的平均絕對差，0–1；越大越不一樣）。
通過標準（spec §3.5）：平均缺口 ≤ 11.5、平均過小區域 ≤ 99、0 人／動物（目視）、兩張明顯不同（目視＋diff 明顯高於 V0）。

用法（repo 根目錄）：PY="env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python"
  $PY run.py gen V0 VA VB VC && $PY run.py report
"""
import csv, io, json, subprocess, sys, time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path("/ai-data/repos/coloring-books")
sys.path.insert(0, str(REPO / "backend"))
from app.services.prompts import qwen_background_prompt  # noqa: E402
from app.services.lineart_render import LineDetail, ink_to_png, render_base  # noqa: E402
from app.services.qa import evaluate  # noqa: E402
from app.services.workflow_registry import WorkflowRegistry  # noqa: E402

C = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
OUT = HERE / "processed"
W, H = 864, 1216
CONTENT_PX = (2244, 3272)
LINE_W = 11
SEEDS = [101, 202]
SCENES = {
    "park": "a sunny park playground with a swing set, trees and flowers",
    "kitchen": "a cozy kitchen with cupboards, an oven, a table with a bowl and cookies",
    "bedroom": "a cozy bedroom with a bed, a bookshelf and a window",
    "beach": "a sunny beach with a sandcastle, a bucket and a beach umbrella",
    "classroom": "a classroom with desks, a blackboard and a window",
    "forest": "a forest path with big trees and mushrooms",
}
VARIANTS: dict[str, list[str]] = {
    "V0": [],
    # 視角
    "VA": ["Wide view from the front", "Seen from a little to the left", "Seen from a little to the right",
           "Close view, the main objects are bigger"],
    # 主體位置
    "VB": ["The main objects are on the left side", "The main objects are on the right side",
           "The main objects are in the middle, seen from a low angle", "The main objects are in the back, open space in front"],
    # 視角＋距離混合
    "VC": ["Wide view from a little to the left", "Close view from a little to the right",
           "Wide view from the front, seen from a low angle", "Close view from the front"],
}

cli = httpx.Client(timeout=900)
ENTRY = WorkflowRegistry(REPO / "comfy_workflows").load().get("qwen_background")


def run(wf: dict) -> tuple[Image.Image, float]:
    t = time.time()
    res = cli.post(f"{C}/prompt", json={"prompt": wf})
    if res.status_code != 200:
        raise RuntimeError(res.text[:800])
    pid = res.json()["prompt_id"]
    while True:
        h = cli.get(f"{C}/history/{pid}").json()
        if pid in h:
            st = h[pid]["status"]
            if st.get("status_str") == "error":
                raise RuntimeError(json.dumps(st.get("messages", []))[:1500])
            for o in h[pid]["outputs"].values():
                for im in o.get("images", []):
                    return Image.open(io.BytesIO(cli.get(f"{C}/view", params=im).content)), round(time.time() - t, 1)
        time.sleep(0.25)


def gpu_idle_wait():
    while True:
        u = int(subprocess.check_output(["nvidia-smi", "--query-gpu=utilization.gpu",
                                         "--format=csv,noheader,nounits"]).decode().split()[0])
        q = cli.get(f"{C}/queue").json()
        if u <= 10 and not q["queue_running"] and not q["queue_pending"]:
            return
        print("GPU busy, waiting 30 s", flush=True)
        time.sleep(30)


def analyse(img: Image.Image, stem: str) -> dict:
    ink, _ = render_base(np.asarray(img.convert("RGB")), CONTENT_PX, LineDetail.STANDARD, LINE_W)
    (OUT / f"{stem}_lineart.png").write_bytes(ink_to_png(ink))
    rep = evaluate(ink, LINE_W)
    return {"gaps": rep.gap_count, "small_regions": rep.metrics["tiny_region_count"],
            "ink_ratio": round(float(ink.mean()), 4)}


def metrics_path() -> Path:
    return OUT / "metrics.json"


def load() -> dict:
    return json.loads(metrics_path().read_text()) if metrics_path().exists() else {}


def gen(ver: str):
    m = load()
    gpu_idle_wait()
    var = VARIANTS[ver]
    if not any(r["variant"] == "warm" for r in m.values()):
        built = ENTRY.build_prompt({"prompt": qwen_background_prompt("a quiet room"), "seed": 999, "width": W, "height": H})
        _, dt = run(built.prompt)
        m["warm"] = {"variant": "warm", "time_s": dt}
        print("warm-up", dt, flush=True)
    for i, (k, scene) in enumerate(SCENES.items()):
        for j, s in enumerate(SEEDS):
            stem = f"{ver}__{k}__s{s}"
            if stem in m:
                continue
            v = var[(i + j) % len(var)] if var else None
            p = qwen_background_prompt(scene, v)
            built = ENTRY.build_prompt({"prompt": p, "seed": s, "width": W, "height": H})
            img, dt = run(built.prompt)
            img = img.convert("RGB")
            img.save(OUT / f"{stem}.png")
            m[stem] = {"variant": ver, "scene": k, "seed": s, "variation": v, "time_s": dt,
                       **analyse(img, stem), "prompt": p}
            metrics_path().write_text(json.dumps(m, indent=1, ensure_ascii=False))
            print(stem, {a: b for a, b in m[stem].items() if a != "prompt"}, flush=True)


def small(stem: str) -> np.ndarray:
    im = Image.open(OUT / f"{stem}_lineart.png").convert("L").resize((96, 136), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32) / 255.0


FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)


def sheet(vers: list[str], suffix: str, name: str):
    cols = [(v, s) for v in vers for s in SEEDS]
    lw, cw, ch, lab = 90, 160, 225, 22
    img = Image.new("RGB", (lw + len(cols) * cw, lab + len(SCENES) * ch), "white")
    d = ImageDraw.Draw(img)
    for j, (v, s) in enumerate(cols):
        d.text((lw + j * cw + 4, 3), f"{v} s{s}", fill="black", font=FONT)
    for i, k in enumerate(SCENES):
        y = lab + i * ch
        d.text((4, y + ch // 2), k, fill="black", font=FONT)
        for j, (v, s) in enumerate(cols):
            p = OUT / f"{v}__{k}__s{s}{suffix}.png"
            if p.exists():
                t = Image.open(p).convert("RGB"); t.thumbnail((cw - 6, ch - 6)); img.paste(t, (lw + j * cw, y))
    img.save(OUT / name, quality=85)
    print(OUT / name, img.size)


def report():
    m = load()
    rows = [r | {"stem": st} for st, r in m.items() if r["variant"] != "warm"]
    with (HERE / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["stem", "variant", "scene", "seed", "variation", "time_s", "gaps", "small_regions",
                                "ink_ratio", "prompt"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in w.fieldnames})
    vers = [v for v in VARIANTS if any(r["variant"] == v for r in rows)]
    for v in vers:
        rs = [r for r in rows if r["variant"] == v]
        diffs = [float(np.abs(small(f"{v}__{k}__s101") - small(f"{v}__{k}__s202")).mean())
                 for k in SCENES if f"{v}__{k}__s101" in m and f"{v}__{k}__s202" in m]
        print(v, "n", len(rs), "gaps", round(np.mean([r["gaps"] for r in rs]), 1),
              "small", round(np.mean([r["small_regions"] for r in rs]), 1),
              "time", round(np.mean([r["time_s"] for r in rs]), 1),
              "pair_diff", round(float(np.mean(diffs)), 4) if diffs else None)
    sheet(vers, "", "sheet.jpg")
    sheet(vers, "_lineart", "sheet_lineart.jpg")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "gen":
        for v in sys.argv[2:]:
            gen(v)
    elif sys.argv[1] == "report":
        report()
