"""Phase 9 spike：只用 Qwen-Image-Edit-2511（拿掉 SDXL）可不可行？中文提示詞可不可以？拋棄式實驗腳本。

A. 角色參考圖：Qwen 純文字生圖（正式 qwen_background workflow，同一張圖，只換提示詞）vs 現行 SDXL（sdxl_text2img +
   prompts.character_prompt + CHARACTER_NEGATIVE）。4 個角色描述 × EN／ZH × 4 seed（app 一次 4 張候選，seed 連號）。
B. 下游：每個描述挑最佳 Qwen EN／Qwen ZH／SDXL EN 參考圖 → 正式 qwen_edit_pose_cutout × 2 個動作（prompts.qwen_pose_prompt）。
C. 中文：qwen_background 用中文場景（V0 不加變化語、VB 變化語，2 seed）；角色動作用中文動作（英文樣板內／全中文樣板）。

用法（repo 根目錄）：PY="env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python"
  $PY run.py sdxl            # SDXL 角色基準（先跑，之後只換一次模型到 Qwen）
  $PY run.py qchar QEN QZM QZH [QENV QZHV]
  $PY run.py pose            # B（需先填 PICKS）＋ C 的中文動作
  $PY run.py bg              # C 的中文背景
  $PY run.py report          # results.csv、指標摘要、總覽圖
"""
import csv, io, itertools, json, subprocess, sys, time, uuid
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path("/ai-data/repos/coloring-books")
sys.path.insert(0, str(REPO / "backend"))
from app.services.lineart_render import LineDetail, ink_to_png, render_base  # noqa: E402
from app.services.prompts import (  # noqa: E402
    CHARACTER_NEGATIVE, QWEN_BACKGROUND_VARIATIONS, character_prompt, qwen_background_prompt, qwen_pose_prompt)
from app.services.qa import evaluate  # noqa: E402
from app.services.workflow_registry import WorkflowRegistry  # noqa: E402

C = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
OUT = HERE / "processed"
P8 = HERE.parent / "phase8_qwen_background_variation_20261110" / "processed"
CHAR_SIZE = (832, 1216)       # api/books.py CHARACTER_SIZE
POSE_SIZE = (832, 1216)       # prompts.POSE_SIZE
BG_SIZE = (864, 1216)         # A4 印刷店 generation_size（同 phase7/8）
CONTENT_PX = (2244, 3272)
LINE_W = 11
CHAR_SEEDS = [1001, 1002, 1003, 1004]   # app：base + i
DESCS = {
    "bunny": ("a small round white bunny with long ears, wearing a red scarf",
              "一隻圓圓的小白兔，長耳朵，圍著紅色圍巾"),
    "girl": ("a little girl with two pigtails wearing a yellow raincoat and rain boots",
             "綁兩條辮子的小女孩，穿黃色雨衣和雨鞋"),
    "dino": ("a small friendly blue dinosaur with round spots", "一隻友善的小藍恐龍，身上有圓點"),
    "astro": ("a little boy astronaut in a white space suit with a round helmet",
              "穿白色太空衣、戴圓頭盔的小男孩太空人"),
}

# --- Qwen 角色樣板（cfg 1 → 排除條件寫在正向）---
QCHAR_EN = ("Single full body front view of {desc}, standing. {variation}Children's picture book illustration, "
            "flat colors, thick clean black outlines, simple shapes. Plain pure white background, centered, whole body "
            "visible from head to feet. Only one character, no other characters, no character sheet, no multiple "
            "views, no text.")
QCHAR_ZH = ("{desc}。單一角色的全身正面圖，站立。{variation}兒童繪本插畫，平塗色彩，粗而乾淨的黑色輪廓線，簡單的造型。"
            "純白色背景，角色置中，從頭到腳整個身體都在畫面內。只有這一個角色，沒有其他角色，不是角色設定圖，"
            "沒有多個視角，沒有文字。")
# 候選變化語（體型＋表情／手勢；一張一個，4 張候選各不同）
CHAR_VAR_EN = ["Big head and small body, arms relaxed at the sides, gentle smile",
               "Slightly taller proportions, waving one hand, big smile",
               "Chubby round shape, hands together in front, shy smile",
               "Arms open wide, cheerful laughing face"]
CHAR_VAR_ZH = ["大頭小身體，雙手自然垂在身體兩側，溫柔的微笑",
               "身形稍微高一點，揮著一隻手，大大的笑容",
               "圓滾滾的體型，雙手在身前合起來，害羞的微笑",
               "張開雙臂，開心大笑的表情"]
CHAR_VERS = {  # ver -> (engine, lang, template, variations)
    "SDXL_EN": ("sdxl", 0, None, None),
    "SDXL_ZH": ("sdxl", 1, None, None),
    "QEN": ("qwen", 0, QCHAR_EN, None),
    "QZM": ("qwen", 1, QCHAR_EN, None),      # 中文描述放進英文樣板
    "QZH": ("qwen", 1, QCHAR_ZH, None),      # 全中文樣板
    "QENV": ("qwen", 0, QCHAR_EN, CHAR_VAR_EN),
    "QZHV": ("qwen", 1, QCHAR_ZH, CHAR_VAR_ZH),
}


def char_prompt_for(ver: str, desc_key: str, i: int) -> str:
    eng, lang, tmpl, var = CHAR_VERS[ver]
    d = DESCS[desc_key][lang]
    if eng == "sdxl":
        return character_prompt(d)
    v = ""
    if var:
        v = var[i % len(var)] + ("。" if tmpl is QCHAR_ZH else ". ")
    return tmpl.format(desc=d, variation=v)


# --- 角色動作 ---
ACTIONS = {"wave": ("waving one hand", "揮手"), "jump": ("jumping happily", "跳起來")}
POSE_ZH_TEMPLATE = ("讓這個角色{action}。保持完全相同的角色設計、比例、顏色、圍巾和耳朵不變。全身，置中，純白色背景，"
                    "沒有影子，沒有投影，兒童繪本插畫，平塗色彩，粗而乾淨的黑色輪廓線。")
POSE_SEED = 101
# 目視總覽後挑的參考圖：desc -> {角色版本: seed}
PICKS: dict[str, dict[str, int]] = {
    "bunny": {"QEN": 1002, "QZH": 1002, "SDXL_EN": 1003},
    "girl": {"QEN": 1001, "QZH": 1002, "SDXL_EN": 1001},
    "dino": {"QEN": 1001, "QZH": 1002, "SDXL_EN": 1003},
    "astro": {"QEN": 1002, "QZH": 1002, "SDXL_EN": 1003},
}
POSE_ZH_DESCS = ["bunny", "girl"]  # C：中文動作測試用的角色（參考圖＝PICKS 的 QEN）

# --- 中文背景（phase8 的 6 場景，自然翻譯）---
SCENES_ZH = {
    "park": "陽光公園的遊樂場，有鞦韆、大樹和花",
    "kitchen": "溫馨的廚房，有櫥櫃、烤箱，桌上有一個碗和餅乾",
    "bedroom": "溫馨的臥室，有一張床、一個書架和一扇窗戶",
    "beach": "陽光海灘，有沙堡、水桶和海灘傘",
    "classroom": "教室，有課桌、黑板和窗戶",
    "forest": "森林小路，有大樹和蘑菇",
}
BG_SEEDS = [101, 202]

cli = httpx.Client(timeout=900)
REG = WorkflowRegistry(REPO / "comfy_workflows").load()


def run(wf: dict) -> tuple[dict[str, Image.Image], float]:
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
            out = {}
            for nid, o in h[pid]["outputs"].items():
                for im in o.get("images", []):
                    out[wf[nid]["_meta"]["title"]] = Image.open(io.BytesIO(cli.get(f"{C}/view", params=im).content))
            if out:
                return out, round(time.time() - t, 1)
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


def upload(p: Path) -> str:
    r = cli.post(f"{C}/upload/image", files={"image": (f"p9_{uuid.uuid4().hex[:8]}.png", p.read_bytes(),
                                                       "image/png")}, data={"overwrite": "true"})
    r.raise_for_status()
    return r.json()["name"]


MPATH = OUT / "metrics.json"


def load() -> dict:
    return json.loads(MPATH.read_text()) if MPATH.exists() else {}


def save(m: dict):
    MPATH.write_text(json.dumps(m, indent=1, ensure_ascii=False))


# --- 指標 ---
def char_metrics(img: Image.Image) -> dict:
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    mn = a.min(axis=2)
    white = mn > 235
    b = 24
    band = np.concatenate([white[:b].ravel(), white[-b:].ravel(), white[b:-b, :b].ravel(), white[b:-b, -b:].ravel()])
    fg = (~white).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    area = fg.size
    big = [k for k in range(1, n) if stats[k, cv2.CC_STAT_AREA] >= 0.01 * area]
    h, w = mn.shape
    if big:
        x0 = min(stats[k, cv2.CC_STAT_LEFT] for k in big)
        y0 = min(stats[k, cv2.CC_STAT_TOP] for k in big)
        x1 = max(stats[k, cv2.CC_STAT_LEFT] + stats[k, cv2.CC_STAT_WIDTH] for k in big)
        y1 = max(stats[k, cv2.CC_STAT_TOP] + stats[k, cv2.CC_STAT_HEIGHT] for k in big)
        touch = bool(y0 <= 4 or y1 >= h - 4 or x0 <= 4 or x1 >= w - 4)
        fill = round(float(sum(stats[k, cv2.CC_STAT_AREA] for k in big)) / area, 3)
        bbox_h = round((y1 - y0) / h, 3)
    else:
        touch, fill, bbox_h = False, 0.0, 0.0
    return {"border_white": round(float(band.mean()), 3), "figures": len(big), "touches_edge": touch,
            "fg_ratio": fill, "bbox_h": bbox_h}


def largest(mask: np.ndarray) -> tuple[np.ndarray, int]:
    b = (mask > 128).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    if n <= 1:
        return np.zeros_like(mask), 0
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    big = int((stats[1:, cv2.CC_STAT_AREA] >= 0.005 * b.sum()).sum())
    return np.where(lab == k, 255, 0).astype(np.uint8), big


def pose_metrics(img: Image.Image, mask: Image.Image, stem: str) -> dict:
    m, big_before = largest(np.asarray(mask.convert("L")))
    a = m.astype(np.float32)[..., None] / 255.0
    rgb = np.asarray(img.convert("RGB"), np.float32)
    cut = (rgb * a + 255.0 * (1 - a)).clip(0, 255).astype(np.uint8)
    Image.fromarray(cut).save(OUT / f"{stem}_cut.png")
    ink, _ = render_base(cut, CONTENT_PX, LineDetail.STANDARD, LINE_W)
    rep = evaluate(ink, LINE_W)
    return {"pieces_before": big_before, "mask_area_ratio": round(float((m > 0).mean()), 4),
            "gaps": rep.gap_count, "small_regions": rep.metrics["tiny_region_count"]}


def line_metrics(img: Image.Image, stem: str) -> dict:
    ink, _ = render_base(np.asarray(img.convert("RGB")), CONTENT_PX, LineDetail.STANDARD, LINE_W)
    (OUT / f"{stem}_lineart.png").write_bytes(ink_to_png(ink))
    rep = evaluate(ink, LINE_W)
    return {"gaps": rep.gap_count, "small_regions": rep.metrics["tiny_region_count"],
            "ink_ratio": round(float(ink.mean()), 4)}


# --- 產生 ---
def gen_char(vers: list[str], descs: list[str] | None = None):
    m = load()
    for ver in vers:
        gpu_idle_wait()
        eng = CHAR_VERS[ver][0]
        entry = REG.get("sdxl_text2img" if eng == "sdxl" else "qwen_background")
        seeds = CHAR_SEEDS if ver != "SDXL_ZH" else CHAR_SEEDS[:1]
        warm = f"warm__{eng}"
        if warm not in m:
            vals = {"prompt": "a red apple", "seed": 1, "width": CHAR_SIZE[0], "height": CHAR_SIZE[1]}
            _, dt = run(entry.build_prompt(vals).prompt)
            m[warm] = {"group": "warm", "time_s": dt}
            save(m)
            print(warm, dt, flush=True)
        for d in descs or DESCS:
            for i, s in enumerate(seeds):
                stem = f"char__{ver}__{d}__s{s}"
                if stem in m:
                    continue
                p = char_prompt_for(ver, d, i)
                vals = {"prompt": p, "seed": s, "width": CHAR_SIZE[0], "height": CHAR_SIZE[1]}
                if eng == "sdxl":
                    vals["negative_prompt"] = CHARACTER_NEGATIVE
                outs, dt = run(entry.build_prompt(vals).prompt)
                img = outs["OUTPUT"].convert("RGB")
                img.save(OUT / f"{stem}.png")
                m[stem] = {"group": "char", "ver": ver, "desc": d, "seed": s, "time_s": dt, **char_metrics(img),
                           "prompt": p}
                save(m)
                print(stem, {k: v for k, v in m[stem].items() if k != "prompt"}, flush=True)


def pose_jobs() -> list[tuple[str, str, str, str, str]]:
    """(stem, ref_stem, action_key, tmpl, prompt)"""
    jobs = []
    for d, picks in PICKS.items():
        for rv, s in picks.items():
            ref = f"char__{rv}__{d}__s{s}"
            for ak, (en, zh) in ACTIONS.items():
                jobs.append((f"pose__{rv}__{d}__{ak}__EN", ref, ak, "EN", qwen_pose_prompt(en)))
                if rv == "QEN" and d in POSE_ZH_DESCS:
                    jobs.append((f"pose__{rv}__{d}__{ak}__ZM", ref, ak, "ZM", qwen_pose_prompt(zh)))
                    jobs.append((f"pose__{rv}__{d}__{ak}__ZH", ref, ak, "ZH", POSE_ZH_TEMPLATE.format(action=zh)))
    return jobs


def gen_pose():
    m = load()
    entry = REG.get("qwen_edit_pose_cutout")
    gpu_idle_wait()
    uploaded: dict[str, str] = {}
    for stem, ref, ak, tmpl, p in pose_jobs():
        if stem in m:
            continue
        if ref not in uploaded:
            uploaded[ref] = upload(OUT / f"{ref}.png")
        vals = {"prompt": p, "seed": POSE_SEED, "width": POSE_SIZE[0], "height": POSE_SIZE[1],
                "character_image": uploaded[ref]}
        outs, dt = run(entry.build_prompt(vals).prompt)
        img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
        img.save(OUT / f"{stem}.png")
        mask.save(OUT / f"{stem}_mask.png")
        _, rv, d, _, _ = stem.split("__")
        m[stem] = {"group": "pose", "ver": rv, "desc": d, "action": ak, "tmpl": tmpl, "ref": ref, "seed": POSE_SEED,
                   "time_s": dt, **pose_metrics(img, mask, stem), "prompt": p}
        save(m)
        print(stem, {k: v for k, v in m[stem].items() if k != "prompt"}, flush=True)


def fetch_masks():
    """gen_pose 第一版沒存 MASK：從 ComfyUI /history 依執行順序對回 pose_jobs()（逐一比對提示詞）後下載。"""
    h = cli.get(f"{C}/history", params={"max_items": 300}).json()
    ents = []
    for pid, e in h.items():
        num, graph = e["prompt"][0], e["prompt"][2]
        refs = [n["inputs"].get("image") for n in graph.values() if n.get("_meta", {}).get("title") == "CHARACTER_REF"]
        if refs and str(refs[0]).startswith("p9_"):
            pos = next(n["inputs"]["prompt"] for n in graph.values() if n.get("_meta", {}).get("title") == "POSITIVE")
            mask_nid = next(k for k, n in graph.items() if n.get("_meta", {}).get("title") == "MASK")
            ents.append((num, pos, e["outputs"][mask_nid]["images"][0]))
    ents.sort(key=lambda x: x[0])
    jobs = pose_jobs()
    assert len(ents) == len(jobs) and all(a[1] == b[4] for a, b in zip(ents, jobs))
    for (_, _, im), job in zip(ents, jobs):
        (OUT / f"{job[0]}_mask.png").write_bytes(cli.get(f"{C}/view", params=im).content)
    print("masks", len(jobs))


def mask_holes():
    """BiRefNet 遮罩在角色內部的破洞（例：白兔的臉、太空人頭盔內的臉被當成背景）。
    hole_ratio＝最大連通塊補洞後多出的面積 / 補洞後面積；hole_ink＝破洞內原圖有墨色（min<200）的像素比例。
    另存「補洞」版去背（_cutfill），看 compose.largest_component 若加補洞能不能救回。"""
    m = load()
    for st, r in m.items():
        if r.get("group") != "pose" or not (OUT / f"{st}_mask.png").exists():
            continue
        lc, _ = largest(np.asarray(Image.open(OUT / f"{st}_mask.png").convert("L")))
        b = (lc > 0).astype(np.uint8)
        ff = b.copy()
        hh, ww = ff.shape
        flood = np.zeros((hh + 2, ww + 2), np.uint8)
        cv2.floodFill(ff, flood, (0, 0), 1)
        filled = b | (1 - ff)
        hole = filled.astype(bool) & ~b.astype(bool)
        rgb = np.asarray(Image.open(OUT / f"{st}.png").convert("RGB"))
        r["hole_ratio"] = round(float(hole.sum()) / max(1, int(filled.sum())), 4)
        r["hole_ink"] = round(float((rgb.min(axis=2)[hole] < 200).sum()) / max(1, int(filled.sum())), 4)
        a = filled.astype(np.float32)[..., None]
        Image.fromarray((rgb * a + 255 * (1 - a)).astype(np.uint8)).save(OUT / f"{st}_cutfill.png")
        print(st, r["hole_ratio"], r["hole_ink"])
    save(m)


def gen_bg():
    m = load()
    entry = REG.get("qwen_background")
    gpu_idle_wait()
    var = list(QWEN_BACKGROUND_VARIATIONS)
    for ver in ["Z0", "ZB"]:
        for i, (k, scene) in enumerate(SCENES_ZH.items()):
            for j, s in enumerate(BG_SEEDS):
                stem = f"bg__{ver}__{k}__s{s}"
                if stem in m:
                    continue
                v = var[(i + j) % len(var)] if ver == "ZB" else None   # 與 phase8 VB 同一個分配
                p = qwen_background_prompt(scene, v)
                outs, dt = run(entry.build_prompt({"prompt": p, "seed": s, "width": BG_SIZE[0],
                                                   "height": BG_SIZE[1]}).prompt)
                img = outs["OUTPUT"].convert("RGB")
                img.save(OUT / f"{stem}.png")
                m[stem] = {"group": "bg", "ver": ver, "scene": k, "seed": s, "variation": v, "time_s": dt,
                           **line_metrics(img, stem), "prompt": p}
                save(m)
                print(stem, {a: b for a, b in m[stem].items() if a != "prompt"}, flush=True)


def free():
    r = cli.post(f"{C}/free", json={"unload_models": True, "free_memory": True})
    print("free", r.status_code)


# --- 報告 ---
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)


def thumb(p: Path, size) -> Image.Image | None:
    if not p.exists():
        return None
    t = Image.open(p).convert("RGB")
    t.thumbnail(size)
    return t


def grid(rows: list[tuple[str, list[Path | None]]], col_labels: list[str], name: str, cw=150, ch=220, lw=70):
    lab = 22
    img = Image.new("RGB", (lw + len(col_labels) * cw, lab + len(rows) * (ch + 4)), (235, 235, 235))
    d = ImageDraw.Draw(img)
    for j, c in enumerate(col_labels):
        d.text((lw + j * cw + 3, 3), c, fill="black", font=FONT)
    for i, (rl, paths) in enumerate(rows):
        y = lab + i * (ch + 4)
        d.text((3, y + ch // 2), rl, fill="black", font=FONT)
        for j, p in enumerate(paths):
            t = thumb(p, (cw - 4, ch)) if p else None
            if t:
                img.paste(t, (lw + j * cw + 2, y))
    img.save(OUT / name, quality=88)
    print(OUT / name, img.size)


def small(p: Path, size=(64, 94)) -> np.ndarray:
    return np.asarray(Image.open(p).convert("L").resize(size, Image.BILINEAR), np.float32) / 255.0


def diversity(ver: str, d: str) -> float | None:
    ps = [OUT / f"char__{ver}__{d}__s{s}.png" for s in CHAR_SEEDS]
    ps = [p for p in ps if p.exists()]
    if len(ps) < 2:
        return None
    return round(float(np.mean([np.abs(small(a) - small(b)).mean() for a, b in itertools.combinations(ps, 2)])), 4)


SCORES_PATH = OUT / "scores.json"   # 目視評分（看總覽圖後手填；內容已併入 results.csv）


def report():
    m = load()
    scores = json.loads(SCORES_PATH.read_text()) if SCORES_PATH.exists() else {}
    rows = [r | {"stem": st} | scores.get(st, {}) for st, r in m.items() if r.get("group") != "warm"]
    fields = ["stem", "group", "ver", "desc", "scene", "action", "tmpl", "seed", "variation", "ref", "time_s",
              "border_white", "figures", "touches_edge", "fg_ratio", "bbox_h", "full_body", "multi_view", "visual",
              "pieces_before", "mask_area_ratio", "hole_ratio", "hole_ink", "gaps", "small_regions", "ink_ratio", "consistency", "action_ok",
              "note", "prompt"]
    with (HERE / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})
    # 角色摘要
    vers = [v for v in CHAR_VERS if any(r.get("ver") == v and r["group"] == "char" for r in rows)]
    for v in vers:
        rs = [r for r in rows if r["group"] == "char" and r["ver"] == v]
        divs = [diversity(v, d) for d in DESCS]
        divs = [x for x in divs if x is not None]
        vis = [r["visual"] for r in rs if r.get("visual") is not None]
        print(f"{v:8s} n={len(rs):2d} border_white={np.mean([r['border_white'] for r in rs]):.3f} "
              f"single={sum(r['figures'] == 1 for r in rs)}/{len(rs)} "
              f"no_edge={sum(not r['touches_edge'] for r in rs)}/{len(rs)} "
              f"time={np.mean([r['time_s'] for r in rs]):.1f} div={np.mean(divs) if divs else 0:.4f} "
              f"visual={np.mean(vis) if vis else float('nan'):.2f}")
    # 總覽：角色
    for vs, name in [(["SDXL_EN", "QEN", "QZH"], "sheet_characters.jpg"),
                     (["QZM", "QZH", "SDXL_ZH"], "sheet_characters_zh_variants.jpg"),
                     (["QEN", "QENV", "QZHV"], "sheet_characters_variation.jpg")]:
        vs = [v for v in vs if v in vers]
        cols = [(v, s) for v in vs for s in (CHAR_SEEDS if v != "SDXL_ZH" else CHAR_SEEDS[:1])]
        grid([(d, [OUT / f"char__{v}__{d}__s{s}.png" for v, s in cols]) for d in DESCS],
             [f"{v} {s % 1000}" for v, s in cols], name, cw=130, ch=190)
    # 總覽：動作（B：原始 OUTPUT；另一張比對 BiRefNet 遮罩破洞 cut vs 補洞 cutfill）＋ C 的中文動作
    prs = [r for r in rows if r["group"] == "pose"]
    if prs:
        refs = ["SDXL_EN", "QEN", "QZH"]
        grid([(d, [OUT / f"char__{rv}__{d}__s{p[rv]}.png" if ak is None else OUT / f"pose__{rv}__{d}__{ak}__EN.png"
                   for rv in refs for ak in (None, *ACTIONS)]) for d, p in PICKS.items()],
             [f"{rv} {c}" for rv in ("SDXL", "QEN", "QZH") for c in ("ref", "wave", "jump")],
             "sheet_poses.jpg", cw=150, ch=220, lw=55)
        grid([(d, [OUT / f"pose__{rv}__{d}__{ak}__EN_{s}.png" for rv in ("QEN", "QZH") for ak in ACTIONS
                   for s in ("cut", "cutfill")]) for d in PICKS],
             [f"{rv} {ak[0]} {s}" for rv in ("QEN", "QZH") for ak in ACTIONS for s in ("cut", "fill")],
             "sheet_poses_mask_holes.jpg", cw=150, ch=220, lw=55)
        rows_c = []
        for d in POSE_ZH_DESCS:
            ref = OUT / f"char__QEN__{d}__s{PICKS[d]['QEN']}.png"
            for ak in ACTIONS:
                rows_c.append((f"{d}\n{ak}", [ref] + [OUT / f"pose__QEN__{d}__{ak}__{t}.png" for t in ("EN", "ZM", "ZH")]))
        grid(rows_c, ["reference", "EN action", "ZH in EN tmpl", "ZH template"], "sheet_poses_zh.jpg",
             cw=200, ch=290, lw=60)
    # 總覽：中文背景 vs phase8 英文
    brs = [r for r in rows if r["group"] == "bg"]
    if brs:
        cols = ["EN V0 101", "EN V0 202", "ZH Z0 101", "ZH Z0 202", "EN VB 101", "EN VB 202", "ZH ZB 101", "ZH ZB 202"]
        rows_bg = []
        rows_bgl = []
        for k in SCENES_ZH:
            ps = []
            for src, v in [("p8", "V0"), ("p9", "Z0"), ("p8", "VB"), ("p9", "ZB")]:
                for s in BG_SEEDS:
                    ps.append(P8 / f"{v}__{k}__s{s}.png" if src == "p8" else OUT / f"bg__{v}__{k}__s{s}.png")
            rows_bg.append((k, ps))
            rows_bgl.append((k, [p.with_name(p.stem + "_lineart.png") for p in ps]))
        grid(rows_bg, cols, "sheet_backgrounds_zh.jpg", cw=140, ch=200, lw=90)
        grid(rows_bgl, cols, "sheet_backgrounds_zh_lineart.jpg", cw=140, ch=200, lw=90)
        p8 = json.loads((P8 / "metrics.json").read_text())
        for v, src in [("V0", p8), ("VB", p8)]:
            rs = [r for r in src.values() if r.get("variant") == v]
            print(f"EN {v}: gaps={np.mean([r['gaps'] for r in rs]):.1f} small={np.mean([r['small_regions'] for r in rs]):.1f} "
                  f"time={np.mean([r['time_s'] for r in rs]):.1f}")
        for v in ["Z0", "ZB"]:
            rs = [r for r in brs if r["ver"] == v]
            print(f"ZH {v}: gaps={np.mean([r['gaps'] for r in rs]):.1f} small={np.mean([r['small_regions'] for r in rs]):.1f} "
                  f"time={np.mean([r['time_s'] for r in rs]):.1f}")
    if prs:
        for key in [("ver",), ("tmpl",)]:
            groups: dict = {}
            for r in prs:
                groups.setdefault(tuple(r[k] for k in key), []).append(r)
            for g, rs in groups.items():
                cs = [r["consistency"] for r in rs if r.get("consistency") is not None]
                acs = [r["action_ok"] for r in rs if r.get("action_ok") is not None]
                print("pose", g, "n", len(rs), "time", round(np.mean([r["time_s"] for r in rs]), 1),
                      "gaps", round(np.mean([r["gaps"] for r in rs]), 1),
                      "small", round(np.mean([r["small_regions"] for r in rs]), 1),
                      "pieces>1", sum(r["pieces_before"] > 1 for r in rs),
                      "holes>5%", sum(r.get("hole_ratio", 0) > 0.05 for r in rs),
                      "cons", round(np.mean(cs), 2) if cs else None, "act", round(np.mean(acs), 2) if acs else None)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1]
    if cmd == "sdxl":
        gen_char(["SDXL_EN", "SDXL_ZH"])
    elif cmd == "qchar":
        gen_char(sys.argv[2:])
    elif cmd == "pose":
        gen_pose()
    elif cmd == "bg":
        gen_bg()
    elif cmd == "masks":
        fetch_masks()
        mask_holes()
    elif cmd == "free":
        free()
    elif cmd == "report":
        report()
