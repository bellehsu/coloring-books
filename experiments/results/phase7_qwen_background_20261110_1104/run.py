"""Phase 7 spike：Qwen-Image-Edit-2511 能不能也畫背景（免換模型）？拋棄式實驗腳本，不改產品程式。

比較：
- SDXL：正式 `sdxl_background` workflow，參數與 app 完全相同（background_prompt + BACKGROUND_NEGATIVE、
  全白結構圖、controlnet_strength 0、manifest 預設 steps/cfg/sampler）。
- QWEN：由 qwen_edit_pose_cutout 拿掉 BiRefNet 的文字生圖圖（本檔 qwen_bg_graph）。
  選項 a：TextEncodeQwenImageEditPlus 不接 image1（/object_info：image1～3 皆為 optional）。
  選項 b：image1 = EmptyImage 全白 864×1216，指令 "Draw on this blank page: …"。
尺寸：A4「印刷店」（新書預設）generation_size 864×1216；線稿指標沿用 phase5/phase6：
render_base((2244, 3272), STANDARD, 11) → qa.evaluate。

用法（repo 根目錄）：
  PY="env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python"
  $PY run.py probe                 # 選項 a／b 試跑
  $PY run.py gen SDXL              # 6 場景 × 2 seed（先暖機一張不計）
  $PY run.py gen QA1 [QA2 ...]     # Qwen 版本
  $PY run.py swap                  # 換模型成本
  $PY run.py sheet QA1 && $PY run.py csv QA1
"""
import csv, io, json, subprocess, sys, time, uuid
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path("/ai-data/repos/coloring-books")
sys.path.insert(0, str(REPO / "backend"))
from app.services import prompts as P  # noqa: E402
from app.services.lineart_render import LineDetail, ink_to_png, render_base  # noqa: E402
from app.services.qa import evaluate  # noqa: E402
from app.services.workflow_registry import WorkflowRegistry  # noqa: E402

C = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
OUT = HERE / "processed"
REF = Path("/ai-data/temp/ubuntu24.04/claude-1000/-ai-data-repos-coloring-books/"
           "0571fc5b-d4ca-4525-ba7b-da0150cd1ebc/scratchpad/p5acc/storage/books/1/generations/1.png")
W, H = 864, 1216
CONTENT_PX = (2244, 3272)
LINE_W = 11
SEEDS = [101, 202]
# 前三個沿用 phase5_background_prompt_20261109 的場景文字
SCENES = {
    "park": "a sunny park playground with a swing set, trees and flowers",
    "kitchen": "a cozy kitchen with cupboards, an oven, a table with a bowl and cookies",
    "bedroom": "a cozy bedroom with a bed, a bookshelf and a window",
    "beach": "a sunny beach with a sandcastle, a bucket and a beach umbrella",
    "classroom": "a classroom with desks, a blackboard and a window",
    "forest": "a forest path with big trees and mushrooms",
}
SIMPLE = ("simple coloring book background, a few large simple objects, lots of empty space, "
          "children's picture book illustration, flat colors, thick clean black outlines, simple shapes")
EXCLUDE = "No people, no animals, no characters, no text."
# cfg 1 → 負向無效，排除寫在正向。最多兩種寫法。
QWEN_VARIANTS = {
    # 選項 a：無 image1，描述式
    "QA1": ("a", lambda s: f"{s}. {SIMPLE[0].upper() + SIMPLE[1:]}. Empty scene, {EXCLUDE[0].lower() + EXCLUDE[1:]}"),
    # 選項 b：全白 image1，指令式
    "QB1": ("b", lambda s: f"Draw on this blank page: {s}. {SIMPLE[0].upper() + SIMPLE[1:]}. "
                           f"Fill the whole page with the scene. {EXCLUDE}"),
}
POSE_ACTION = "waving one hand"

cli = httpx.Client(timeout=900)
REG = WorkflowRegistry(REPO / "comfy_workflows").load()


# ---------------------------------------------------------------- ComfyUI helpers
def upload(data: bytes, tag: str) -> str:
    r = cli.post(f"{C}/upload/image", files={"image": (f"p7_{tag}_{uuid.uuid4().hex[:8]}.png", data, "image/png")},
                 data={"overwrite": "true"})
    r.raise_for_status()
    return r.json()["name"]


def white_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), "white").save(buf, format="PNG")
    return buf.getvalue()


def run(wf: dict) -> tuple[dict[str, Image.Image], float]:
    t = time.time()
    res = cli.post(f"{C}/prompt", json={"prompt": wf})
    if res.status_code != 200:
        raise RuntimeError(res.text[:800])
    pid = res.json()["prompt_id"]
    while True:
        h = cli.get(f"{C}/history/{pid}").json()
        if pid in h:
            dt = round(time.time() - t, 1)
            st = h[pid]["status"]
            if st.get("status_str") == "error":
                raise RuntimeError(json.dumps(st.get("messages", []))[:1500])
            out = {}
            for nid, o in h[pid]["outputs"].items():
                for im in o.get("images", []):
                    out[wf[nid]["_meta"]["title"]] = Image.open(io.BytesIO(cli.get(f"{C}/view", params=im).content))
            return out, dt
        time.sleep(0.25)


def gpu_idle_wait():
    while True:
        busy = 0
        for _ in range(3):
            u = int(subprocess.check_output(["nvidia-smi", "--query-gpu=utilization.gpu",
                                             "--format=csv,noheader,nounits"]).decode().split()[0])
            q = cli.get(f"{C}/queue").json()
            if u > 10 or q["queue_running"] or q["queue_pending"]:
                busy += 1
            time.sleep(2)
        if busy < 3:
            return
        print("GPU busy, waiting 60 s", flush=True)
        time.sleep(60)


# ---------------------------------------------------------------- graphs
def qwen_bg_graph(prompt: str, seed: int, option: str) -> dict:
    """qwen_edit_pose_cutout 去掉 BiRefNet／CHARACTER_REF；節點與欄位已用 /object_info 驗證。"""
    pose = json.loads((REPO / "comfy_workflows/qwen_edit_pose_cutout/workflow_api.json").read_text())
    g = {k: pose[k] for k in ("1", "2", "3", "4", "5", "6", "12", "13", "14")}
    g["12"]["inputs"].update(width=W, height=H)
    g["13"]["inputs"]["seed"] = seed
    g["8"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "POSITIVE"},
              "inputs": {"clip": ["2", 0], "prompt": prompt, "vae": ["3", 0]}}
    g["9"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "NEGATIVE"},
              "inputs": {"clip": ["2", 0], "prompt": "", "vae": ["3", 0]}}
    if option == "a":
        g["13"]["inputs"].update(positive=["8", 0], negative=["9", 0])
    else:
        g["7"] = {"class_type": "EmptyImage", "_meta": {"title": "BLANK_PAGE"},
                  "inputs": {"width": W, "height": H, "batch_size": 1, "color": 0xFFFFFF}}
        g["8"]["inputs"]["image1"] = ["7", 0]
        g["9"]["inputs"]["image1"] = ["7", 0]
        g["10"], g["11"] = pose["10"], pose["11"]  # FluxKontextMultiReferenceLatentMethod index_timestep_zero
    g["16"] = {"class_type": "SaveImage", "_meta": {"title": "OUTPUT"},
               "inputs": {"images": ["14", 0], "filename_prefix": "coloring_books/p7_qwen_bg"}}
    return g


_WHITE: str | None = None


def sdxl_bg_graph(scene: str, seed: int) -> dict:
    global _WHITE
    if _WHITE is None:
        _WHITE = upload(white_png(), "white")
    built = REG.get("sdxl_background").build_prompt({
        "prompt": P.background_prompt(scene), "negative_prompt": P.BACKGROUND_NEGATIVE, "seed": seed,
        "width": W, "height": H, "structure_image": _WHITE, "controlnet_strength": 0.0,
        "controlnet_type": "hed/pidi/scribble/ted", "controlnet_end_percent": 0.5})
    return built.prompt


_REFNAME: str | None = None


def pose_graph(seed: int) -> dict:
    global _REFNAME
    if _REFNAME is None:
        _REFNAME = upload(REF.read_bytes(), "ref")
    return REG.get("qwen_edit_pose_cutout").build_prompt({
        "prompt": P.qwen_pose_prompt(POSE_ACTION), "seed": seed, "width": P.POSE_SIZE[0],
        "height": P.POSE_SIZE[1], "character_image": _REFNAME}).prompt


def bg_graph(ver: str, scene_key: str, seed: int) -> tuple[dict, str]:
    scene = SCENES[scene_key]
    if ver == "SDXL":
        return sdxl_bg_graph(scene, seed), P.background_prompt(scene)
    opt, pf = QWEN_VARIANTS[ver]
    return qwen_bg_graph(pf(scene), seed, opt), pf(scene)


# ---------------------------------------------------------------- metrics
def analyse(img: Image.Image, stem: str) -> dict:
    ink, _ = render_base(np.asarray(img.convert("RGB")), CONTENT_PX, LineDetail.STANDARD, LINE_W)
    (OUT / f"{stem}_lineart.png").write_bytes(ink_to_png(ink))
    rep = evaluate(ink, LINE_W)
    return {"gaps": rep.gap_count, "small_regions": rep.metrics["tiny_region_count"],
            "regions": rep.metrics.get("region_count"), "ink_ratio": round(float(ink.mean()), 4)}


def load_metrics() -> dict:
    p = OUT / "metrics.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_metrics(m: dict):
    (OUT / "metrics.json").write_text(json.dumps(m, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- commands
def probe():
    gpu_idle_wait()
    for ver in QWEN_VARIANTS:
        for k in ("park", "kitchen"):
            g, p = bg_graph(ver, k, 101)
            try:
                outs, dt = run(g)
            except Exception as e:  # noqa: BLE001
                print(ver, k, "ERROR", str(e)[:500], flush=True)
                continue
            outs["OUTPUT"].convert("RGB").save(OUT / f"probe_{ver}_{k}.png")
            print(ver, k, dt, "s", flush=True)


def gen(ver: str):
    metrics = load_metrics()
    gpu_idle_wait()
    # 暖機：同家族先跑一張（不同 seed，不計時），確保量到的是「熱」的時間
    g, _ = bg_graph(ver, "park", 999)
    _, dt = run(g)
    print(ver, "warm-up", dt, "s", flush=True)
    for k in SCENES:
        for s in SEEDS:
            stem = f"{ver}__{k}__s{s}"
            g, p = bg_graph(ver, k, s)
            outs, dt = run(g)
            img = outs["OUTPUT"].convert("RGB")
            img.save(OUT / f"{stem}.png")
            metrics[stem] = {"family": "SDXL" if ver == "SDXL" else "QWEN", "variant": ver, "scene": k, "seed": s,
                             "time_s": dt, **analyse(img, stem), "prompt": p}
            save_metrics(metrics)
            print(stem, {k2: v for k2, v in metrics[stem].items() if k2 != "prompt"}, flush=True)


def swap(qwen_ver: str = "QA1", first_rep: str = "0", alt_first: str = "0"):
    """交替（SDXL 背景 → Qwen 動作）×2 vs 只用 Qwen（Qwen 背景 → Qwen 動作）×2。每步換 seed 避免 ComfyUI 快取。
    第一輪：swap QA1（rep0、rep1，先 qwen_only）；第二輪：swap QA1 2 1（rep2、rep3，先 alternating，抵銷順序偏差）。"""
    gpu_idle_wait()
    p = OUT / "swap.json"
    res = json.loads(p.read_text()) if p.exists() else {}
    _, dt = run(pose_graph(5000))  # 起點狀態：Qwen 已載入（模擬上一頁剛做完精準動作）
    print("prime qwen pose", dt, flush=True)
    for rep in range(int(first_rep), int(first_rep) + 2):
        seqs = {
            "qwen_only": [("qwen_bg", lambda i: bg_graph(qwen_ver, ["beach", "forest"][i], 7000 + rep * 10 + i)[0]),
                          ("qwen_pose", lambda i: pose_graph(6000 + rep * 10 + i))],
            "alternating": [("sdxl_bg", lambda i: bg_graph("SDXL", ["beach", "forest"][i], 8000 + rep * 10 + i)[0]),
                            ("qwen_pose", lambda i: pose_graph(9000 + rep * 10 + i))],
        }
        order = ["alternating", "qwen_only"] if alt_first == "1" else ["qwen_only", "alternating"]
        for name in order:
            steps = seqs[name]
            times = []
            t0 = time.time()
            for i in range(2):
                for label, mk in steps:
                    _, dt = run(mk(i))
                    times.append((label, dt))
                    print(f"rep{rep}", name, label, dt, flush=True)
            res[f"{name}_rep{rep}"] = {"steps": times, "total_s": round(time.time() - t0, 1)}
            (OUT / "swap.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


# 目視評分（看過 processed/*.png 後填寫）：(有人或動物?, 分數 1–5, 備註)
VISUAL: dict[str, tuple[bool, int, str]] = {
    "SDXL__park__s101": (False, 2, "灰階陰影大片、圍欄＋爬架＋樹叢很密"),
    "SDXL__park__s202": (False, 2, "前景兩大叢花灌木細碎"),
    "SDXL__kitchen__s101": (False, 2, "櫃子器具很多、磁磚地板格子"),
    "SDXL__kitchen__s202": (False, 2, "鍋碗瓢盆密集"),
    "SDXL__bedroom__s101": (False, 3, "構圖對稱尚可，但灰色牆面與書櫃細節多"),
    "SDXL__bedroom__s202": (False, 2, "床單皺褶、牆上畫框、窗外風景細碎"),
    "SDXL__beach__s101": (False, 2, "沙堡畫成怪桶子、海浪細線多、灰色天空"),
    "SDXL__beach__s202": (False, 2, "海浪與沙灘細線密集"),
    "SDXL__classroom__s101": (False, 2, "大片灰黑塊、窗戶反光線"),
    "SDXL__classroom__s202": (False, 2, "綠色地板、透視歪"),
    "SDXL__forest__s101": (False, 1, "黑色大塊樹幹＋滿版灌木，幾乎無法著色"),
    "SDXL__forest__s202": (False, 1, "滿版蘑菇與枝葉"),
    "QA1__park__s101": (False, 5, "鞦韆＋兩棵樹＋花，大面積留白"),
    "QA1__park__s202": (False, 5, "同上，與 s101 幾乎同構圖"),
    "QA1__kitchen__s101": (False, 4, "物件大而清楚；爐台小零件略多"),
    "QA1__kitchen__s202": (False, 4, "同上"),
    "QA1__bedroom__s101": (False, 4, "床＋書櫃＋窗；書本細節小"),
    "QA1__bedroom__s202": (False, 4, "同上"),
    "QA1__beach__s101": (False, 5, "傘＋沙堡＋桶＋太陽，很適合著色"),
    "QA1__beach__s202": (False, 5, "同上"),
    "QA1__classroom__s101": (False, 4, "黑板大黑塊（線稿為外框，OK）、桌椅略多"),
    "QA1__classroom__s202": (False, 4, "同上"),
    "QA1__forest__s101": (False, 4, "大樹幹＋小路＋蘑菇；樹冠留白多"),
    "QA1__forest__s202": (False, 4, "樹冠上有些小葉片記號"),
    "QB1__park__s101": (False, 4, "花叢比 QA1 密"),
    "QB1__park__s202": (False, 4, "同上，彩色與線稿混搭"),
    "QB1__kitchen__s101": (False, 4, "多一台微波爐細節"),
    "QB1__kitchen__s202": (False, 4, "乾淨"),
    "QB1__bedroom__s101": (False, 4, "乾淨，右側裁切"),
    "QB1__bedroom__s202": (False, 4, "乾淨"),
    "QB1__beach__s101": (False, 4, "太陽在線稿中消失（淡黃）"),
    "QB1__beach__s202": (False, 4, "地平線上有小碎點、半棵椰子樹"),
    "QB1__classroom__s101": (False, 4, "乾淨"),
    "QB1__classroom__s202": (False, 3, "黑板上出現亂碼文字"),
    "QB1__forest__s101": (False, 4, "乾淨"),
    "QB1__forest__s202": (False, 4, "乾淨"),
}

FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)


def sheet(qwen_ver: str, suffix: str = "", name: str = "sheet.jpg"):
    cols = [("SDXL", s) for s in SEEDS] + [(qwen_ver, s) for s in SEEDS]
    lw, cw, ch, lab = 120, 216, 304, 22
    img = Image.new("RGB", (lw + len(cols) * cw, lab + len(SCENES) * ch), "white")
    d = ImageDraw.Draw(img)
    for j, (v, s) in enumerate(cols):
        d.text((lw + j * cw + 4, 3), f"{'SDXL' if v == 'SDXL' else 'QWEN'} s{s}", fill="black", font=FONT)
    for i, k in enumerate(SCENES):
        y = lab + i * ch
        d.text((4, y + ch // 2), k, fill="black", font=FONT)
        for j, (v, s) in enumerate(cols):
            p = OUT / f"{v}__{k}__s{s}{suffix}.png"
            if p.exists():
                t = Image.open(p).convert("RGB"); t.thumbnail((cw - 6, ch - 6)); img.paste(t, (lw + j * cw, y))
    img.save(OUT / name, quality=85)
    print(OUT / name, img.size)


def write_csv(vers: list[str]):
    m = load_metrics()
    rows = []
    for stem, r in m.items():
        if r["variant"] not in ["SDXL", *vers]:
            continue
        has, score, note = VISUAL.get(stem, (None, None, ""))
        rows.append({k: r[k] for k in ("family", "variant", "scene", "seed", "time_s", "gaps", "small_regions",
                                       "regions", "ink_ratio")}
                    | {"people_or_animals": has, "visual_score": score, "visual_note": note, "prompt": r["prompt"]})
    with (HERE / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, rows[0].keys()); w.writeheader(); w.writerows(rows)
    for v in ["SDXL", *vers]:
        rs = [r for r in rows if r["variant"] == v]
        sc = [r["visual_score"] for r in rs if r["visual_score"] is not None]
        print(v, "n", len(rs), "gaps", round(np.mean([r["gaps"] for r in rs]), 1),
              "small", round(np.mean([r["small_regions"] for r in rs]), 1),
              "ink", round(np.mean([r["ink_ratio"] for r in rs]), 3),
              "time", round(np.mean([r["time_s"] for r in rs]), 1),
              "people", sum(1 for r in rs if r["people_or_animals"]),
              "score", round(np.mean(sc), 2) if sc else None)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1]
    if cmd == "probe":
        probe()
    elif cmd == "gen":
        for v in sys.argv[2:]:
            gen(v)
    elif cmd == "swap":
        swap(*sys.argv[2:])
    elif cmd == "sheet":
        sheet(sys.argv[2])
        sheet(sys.argv[2], "_lineart", "sheet_lineart.jpg")
    elif cmd == "csv":
        write_csv(sys.argv[2:])
