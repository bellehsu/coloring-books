"""Phase 9 all-Qwen gate (spec §3: E1 characters, E2 Chinese prompts). Throwaway experiment script.

Reuses the spike in spike/ (phase9_qwen_character_zh_20261110_1502, copied verbatim; images in processed/spike/)
and phase 8's English backgrounds (main checkout, read-only). Runs only what the spike did not cover:

E1 characters: 6 Chinese descriptions × 4 seeds, the user's Chinese inside the English character template
   (the spike's QZM shape), with two variation lists: VA = the spike's QENV list with its proposed fixes,
   VG = a generic list without arms/hands (works for vehicles and objects).
E1 poses: best candidate per description + the book-2 bunny (storage/books/2/generations/51.png, read-only)
   through qwen_edit_pose_cutout with the generalised pose template (G).
E2: 6 actions × {EN, ZH-in-EN-template (ZM), full-ZH template (ZH)} × 2 seeds on the bunny and one Qwen character;
   6 scenes through a full-ZH background template (ZH shape 2) — EN = phase 8 VB, ZH shape 1 = spike ZB.

Usage (worktree root):
  PY="env PYTHONPATH=backend:. /ai-data/venvs/ubuntu24.04/coloring-books/bin/python"
  $PY experiments/results/phase9_all_qwen_20261110_1700/run.py char
  $PY .../run.py pose      # needs PICKS filled after looking at sheet_characters.jpg
  $PY .../run.py bg
  $PY .../run.py free
  $PY .../run.py report
"""
import csv, io, itertools, json, subprocess, sys, time, uuid
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.services.lineart_render import LineDetail, ink_to_png, render_base  # noqa: E402
from app.services.prompts import QWEN_BACKGROUND_VARIATIONS  # noqa: E402
from app.services.qa import evaluate  # noqa: E402
from app.services.workflow_registry import WorkflowRegistry  # noqa: E402

C = "http://127.0.0.1:8188"
OUT = HERE / "processed"
SHEETS = HERE / "sheets"            # downscaled contact sheets (committed)
SPIKE = OUT / "spike"               # spike images (copied, not committed)
P8 = Path("/ai-data/repos/coloring-books/experiments/results/phase8_qwen_background_variation_20261110/processed")
BUNNY = Path("/ai-data/repos/coloring-books/storage/books/2/generations/51.png")
# Phase 6 calibrated on a different SDXL bunny (same description); rerun it on the same 6 actions × 2 seeds, EN only,
# for a like-for-like comparison with Phase 6 V6 (consistency 4.1)
BUNNY_P6 = Path("/ai-data/temp/ubuntu24.04/claude-1000/-ai-data-repos-coloring-books/"
                "0571fc5b-d4ca-4525-ba7b-da0150cd1ebc/scratchpad/p5acc/storage/books/1/generations/1.png")
CHAR_SIZE = (832, 1216)
POSE_SIZE = (832, 1216)
BG_SIZE = (864, 1216)
CONTENT_PX = (2244, 3272)
LINE_W = 11
CHAR_SEEDS = [1001, 1002, 1003, 1004]

# --- E1: six Chinese descriptions (the first four are the spike's) ---
DESCS = {
    "bunny": "一隻圓圓的小白兔，長耳朵，圍著紅色圍巾",                 # animal + accessory
    "girl": "綁兩條辮子的小女孩，穿黃色雨衣和雨鞋",                     # child + clothing
    "dino": "一隻友善的小藍恐龍，身上有圓點",                           # animal, no accessories
    "astro": "穿白色太空衣、戴圓頭盔的小男孩太空人",                     # child + clothing
    "car": "一台紅色的小汽車，有大大的眼睛和笑臉",                       # vehicle character
    "fox": "戴黃色草帽、背著綠色小背包的小狐狸",                         # animal + accessories
}

QCHAR_TEMPLATE = ("Single full body front view of {description}, standing. {variation}Children's picture book "
                  "illustration, flat colors, thick clean black outlines, simple shapes. Plain pure white background, "
                  "no ground, no shadow, centered, whole body visible from head to feet. Only one character, no other "
                  "characters, no character sheet, no multiple views, no text.")
VARS = {
    # VM: phase9_qwen_calibration_20261110（另一個 session）校正通過的三句（無副作用、彼此明顯不同）
    "VM": ("Big head and small body, arms relaxed at the sides, gentle smile",
           "Slightly taller proportions, arms slightly away from the body, big smile",
           "Chubby round shape, hands together in front, happy smile"),
    # generic: proportions / expression / slight turn — no limbs, so it suits vehicles and objects
    "VG": ("Big head and small body, gentle smile",
           "Slightly taller and slimmer proportions, big happy smile",
           "Chubby round shape, cheerful laughing expression",
           "Turned slightly to one side, playful happy expression"),
}


def char_prompt(desc: str, var: str | None) -> str:
    return QCHAR_TEMPLATE.format(description=desc, variation=f"{var}. " if var else "")


# --- poses ---
# 採用 phase9_pose_location_20261110 的最終樣板（通用、不寫死圍巾、即使動作含地點也只畫角色）
POSE_EN = ("Make this exact character {action}. Draw only the character, even if the action mentions a place: no "
           "ground, no floor, no scenery, no background objects. Keep the same character design, proportions, colors, "
           "clothing and accessories unchanged. Full body, centered, plain pure white background, no shadow, no cast "
           "shadow, children's picture book illustration, flat colors, thick clean black outlines.")
POSE_ZH = ("讓這個角色{action}。保持完全相同的角色設計、臉、比例、顏色、服裝和配件不變；不要加上新的服裝或配件。"
           "全身，置中，純白色背景，沒有影子，沒有投影，兒童繪本插畫，平塗色彩，粗而乾淨的黑色輪廓線。")
ACTIONS = {  # key -> (EN preset phrase as in frontend/src/lib/actions.ts, Chinese); same 6 actions as Phase 6
    "sit": ("sitting on the ground", "坐在地上"),
    "hold": ("holding something with both hands", "雙手拿著東西"),
    "run": ("running", "跑步"),
    "wave": ("waving one hand", "揮手"),
    "jump": ("jumping happily", "跳起來"),
    "read": ("reading a book", "看書"),
}
E1_ACTIONS = ["wave", "jump"]
POSE_SEEDS = [101, 202]
# best candidate per description (stem of a char image), filled after looking at sheet_characters.jpg
# 目視 sheet 後挑的參考圖（VG：通用變化語，車子不會長出手腳）
PICKS: dict[str, str] = {
    "bunny": "char__VG__bunny__s1001", "girl": "char__VG__girl__s1001", "dino": "char__VG__dino__s1003",
    "astro": "char__VG__astro__s1003", "car": "char__VG__car__s1001", "fox": "char__VG__fox__s1003",
}
E2_POSE_CHARS = ["bunny51", "girl"]   # bunny51 = the book-2 bunny; girl = PICKS["girl"]

# --- E2 backgrounds: full-Chinese template (shape 2); shape 1 = spike ZB, EN = phase 8 VB ---
SCENES_ZH = {
    "park": "陽光公園的遊樂場，有鞦韆、大樹和花",
    "kitchen": "溫馨的廚房，有櫥櫃、烤箱，桌上有一個碗和餅乾",
    "bedroom": "溫馨的臥室，有一張床、一個書架和一扇窗戶",
    "beach": "陽光海灘，有沙堡、水桶和海灘傘",
    "classroom": "教室，有課桌、黑板和窗戶",
    "forest": "森林小路，有大樹和蘑菇",
}
BG_ZH_TEMPLATE = ("{scene}。{variation}簡單的著色本背景，幾個大而簡單的物件，大量留白，兒童繪本插畫，平塗色彩，"
                  "粗而乾淨的黑色輪廓線，簡單的造型。空無一人的場景，沒有人，沒有動物，沒有角色，沒有文字。")
BG_VAR_ZH = ("主要物件在畫面左邊", "主要物件在畫面右邊", "主要物件在畫面中間，從低角度看", "主要物件在後方，前面是空地")
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
    r = cli.post(f"{C}/upload/image", files={"image": (f"p9g_{uuid.uuid4().hex[:8]}.png", p.read_bytes(),
                                                       "image/png")}, data={"overwrite": "true"})
    r.raise_for_status()
    return r.json()["name"]


MPATH = OUT / "metrics.json"


def load() -> dict:
    return json.loads(MPATH.read_text()) if MPATH.exists() else {}


def save(m: dict):
    MPATH.write_text(json.dumps(m, indent=1, ensure_ascii=False))


# --- metrics (same definitions as the spike) ---
def char_metrics(img: Image.Image) -> dict:
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    white = a.min(axis=2) > 235
    b = 24
    band = np.concatenate([white[:b].ravel(), white[-b:].ravel(), white[b:-b, :b].ravel(), white[b:-b, -b:].ravel()])
    fg = cv2.morphologyEx((~white).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    h, w = white.shape
    big = [k for k in range(1, n) if stats[k, cv2.CC_STAT_AREA] >= 0.01 * fg.size]
    if big:
        x0 = min(stats[k, cv2.CC_STAT_LEFT] for k in big)
        y0 = min(stats[k, cv2.CC_STAT_TOP] for k in big)
        x1 = max(stats[k, cv2.CC_STAT_LEFT] + stats[k, cv2.CC_STAT_WIDTH] for k in big)
        y1 = max(stats[k, cv2.CC_STAT_TOP] + stats[k, cv2.CC_STAT_HEIGHT] for k in big)
        touch = bool(y0 <= 4 or y1 >= h - 4 or x0 <= 4 or x1 >= w - 4)
        bbox_h = round((y1 - y0) / h, 3)
    else:
        touch, bbox_h = False, 0.0
    return {"border_white": round(float(band.mean()), 3), "figures": len(big), "touches_edge": touch,
            "bbox_h": bbox_h}


def filled_largest(mask: np.ndarray) -> tuple[np.ndarray, int, float]:
    """Largest component of the BiRefNet mask with interior holes filled (as the spike's _cutfill, the fix
    the spike recommended for compose.largest_component). Returns (mask01, pieces>=0.5% before LC, hole_ratio)."""
    b = (mask > 128).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    if n <= 1:
        return np.zeros_like(b), 0, 0.0
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    pieces = int((stats[1:, cv2.CC_STAT_AREA] >= 0.005 * b.sum()).sum())
    lc = (lab == k).astype(np.uint8)
    ff = lc.copy()
    cv2.floodFill(ff, np.zeros((ff.shape[0] + 2, ff.shape[1] + 2), np.uint8), (0, 0), 1)
    filled = lc | (1 - ff)
    return filled, pieces, round(float(filled.sum() - lc.sum()) / max(1, int(filled.sum())), 4)


def pose_metrics(img: Image.Image, mask: Image.Image, stem: str) -> dict:
    m, pieces, hole = filled_largest(np.asarray(mask.convert("L")))
    a = m.astype(np.float32)[..., None]
    cut = (np.asarray(img.convert("RGB"), np.float32) * a + 255.0 * (1 - a)).clip(0, 255).astype(np.uint8)
    Image.fromarray(cut).save(OUT / f"{stem}_cut.png")
    ink, _ = render_base(cut, CONTENT_PX, LineDetail.STANDARD, LINE_W)
    rep = evaluate(ink, LINE_W)
    return {"pieces_before": pieces, "hole_ratio": hole, "gaps": rep.gap_count,
            "small_regions": rep.metrics["tiny_region_count"]}


def line_metrics(img: Image.Image, stem: str) -> dict:
    ink, _ = render_base(np.asarray(img.convert("RGB")), CONTENT_PX, LineDetail.STANDARD, LINE_W)
    (OUT / f"{stem}_lineart.png").write_bytes(ink_to_png(ink))
    rep = evaluate(ink, LINE_W)
    return {"gaps": rep.gap_count, "small_regions": rep.metrics["tiny_region_count"],
            "ink_ratio": round(float(ink.mean()), 4)}


def warm(m: dict, entry, key: str, extra: dict | None = None):
    if key in m:
        return
    vals = {"prompt": "a red apple", "seed": 1, "width": CHAR_SIZE[0], "height": CHAR_SIZE[1], **(extra or {})}
    _, dt = run(entry.build_prompt(vals).prompt)
    m[key] = {"group": "warm", "time_s": dt}
    save(m)
    print(key, dt, flush=True)


# --- E1 characters ---
def gen_char(lists: list[str]):
    m = load()
    entry = REG.get("qwen_background")
    gpu_idle_wait()
    warm(m, entry, "warm__qwen")
    for vl in lists:
        for d, desc in DESCS.items():
            for i, s in enumerate(CHAR_SEEDS):
                stem = f"char__{vl}__{d}__s{s}"
                if stem in m:
                    continue
                var = VARS[vl][i % len(VARS[vl])]
                p = char_prompt(desc, var)
                outs, dt = run(entry.build_prompt({"prompt": p, "seed": s, "width": CHAR_SIZE[0],
                                                   "height": CHAR_SIZE[1]}).prompt)
                img = outs["OUTPUT"].convert("RGB")
                img.save(OUT / f"{stem}.png")
                m[stem] = {"group": "char", "ver": vl, "desc": d, "seed": s, "variation": var, "time_s": dt,
                           **char_metrics(img), "prompt": p}
                save(m)
                print(stem, {k: v for k, v in m[stem].items() if k not in ("prompt", "variation")}, flush=True)


# --- poses ---
def ref_path(ch: str) -> Path:
    return {"bunny51": BUNNY, "bunnyP6": BUNNY_P6}.get(ch) or OUT / f"{PICKS[ch]}.png"


def pose_jobs() -> list[tuple[str, str, str, str, int, str]]:
    """(stem, char, action, shape, seed, prompt). E1 = picks × wave/jump EN seed 101; E2 = 6 actions × 3 shapes ×
    2 seeds on E2_POSE_CHARS (E2's EN rows double as E1 rows for those characters)."""
    jobs = []
    for ch in ["bunny51", "bunnyP6", *PICKS]:
        e2 = ch in E2_POSE_CHARS
        for ak, (en, zh) in ACTIONS.items():
            for shape in ("EN", "ZM", "ZH"):
                for s in POSE_SEEDS:
                    if ch == "bunnyP6":
                        if shape != "EN":
                            continue
                    elif not e2 and (ak not in E1_ACTIONS or shape != "EN" or s != POSE_SEEDS[0]):
                        continue
                    p = {"EN": POSE_EN.format(action=en), "ZM": POSE_EN.format(action=zh),
                         "ZH": POSE_ZH.format(action=zh)}[shape]
                    jobs.append((f"pose__{ch}__{ak}__{shape}__s{s}", ch, ak, shape, s, p))
    return jobs


def gen_pose():
    m = load()
    entry = REG.get("qwen_edit_pose_cutout")
    gpu_idle_wait()
    uploaded: dict[str, str] = {}
    jobs = pose_jobs()
    # order: per character, EN first, so E1 numbers arrive early
    jobs.sort(key=lambda j: (j[1] not in ("bunny51", "bunnyP6"), j[3] != "EN", j[1], j[3], j[2], j[4]))
    for stem, ch, ak, shape, s, p in jobs:
        if stem in m:
            continue
        if ch not in uploaded:
            uploaded[ch] = upload(ref_path(ch))
        vals = {"prompt": p, "seed": s, "width": POSE_SIZE[0], "height": POSE_SIZE[1], "character_image": uploaded[ch]}
        outs, dt = run(entry.build_prompt(vals).prompt)
        img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
        img.save(OUT / f"{stem}.png")
        mask.save(OUT / f"{stem}_mask.png")
        m[stem] = {"group": "pose", "char": ch, "action": ak, "shape": shape, "seed": s, "time_s": dt,
                   "ref": str(ref_path(ch).name), **pose_metrics(img, mask, stem), "prompt": p}
        save(m)
        print(stem, {k: v for k, v in m[stem].items() if k != "prompt"}, flush=True)


# --- E2 backgrounds, full-Chinese template ---
def gen_bg():
    m = load()
    entry = REG.get("qwen_background")
    gpu_idle_wait()
    for i, (k, scene) in enumerate(SCENES_ZH.items()):
        for j, s in enumerate(BG_SEEDS):
            stem = f"bg__ZHT__{k}__s{s}"
            if stem in m:
                continue
            v = BG_VAR_ZH[(i + j) % len(BG_VAR_ZH)]   # same assignment as phase 8 VB / spike ZB
            p = BG_ZH_TEMPLATE.format(scene=scene, variation=f"{v}。")
            outs, dt = run(entry.build_prompt({"prompt": p, "seed": s, "width": BG_SIZE[0],
                                               "height": BG_SIZE[1]}).prompt)
            img = outs["OUTPUT"].convert("RGB")
            img.save(OUT / f"{stem}.png")
            m[stem] = {"group": "bg", "ver": "ZHT", "scene": k, "seed": s, "variation": v, "time_s": dt,
                       **line_metrics(img, stem), "prompt": p}
            save(m)
            print(stem, {a: b for a, b in m[stem].items() if a != "prompt"}, flush=True)


def free():
    r = cli.post(f"{C}/free", json={"unload_models": True, "free_memory": True})
    print("free", r.status_code)


# --- report ---
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)


def grid(rows, col_labels, name, cw=150, ch=220, lw=70, dest=None):
    lab = 22
    img = Image.new("RGB", (lw + len(col_labels) * cw, lab + len(rows) * (ch + 4)), (225, 225, 225))
    d = ImageDraw.Draw(img)
    for j, c in enumerate(col_labels):
        d.text((lw + j * cw + 3, 3), c, fill="black", font=FONT)
    for i, (rl, paths) in enumerate(rows):
        y = lab + i * (ch + 4)
        d.text((3, y + ch // 2), rl, fill="black", font=FONT)
        for j, p in enumerate(paths):
            if p and Path(p).exists():
                t = Image.open(p).convert("RGB")
                t.thumbnail((cw - 4, ch))
                img.paste(t, (lw + j * cw + 2, y))
    (dest or SHEETS).mkdir(parents=True, exist_ok=True)
    img.save((dest or SHEETS) / name, quality=82)
    print((dest or SHEETS) / name, img.size)


def small(p: Path, size=(64, 94)) -> np.ndarray:
    return np.asarray(Image.open(p).convert("L").resize(size, Image.BILINEAR), np.float32) / 255.0


def diversity(paths: list[Path]) -> float | None:
    ps = [p for p in paths if p.exists()]
    if len(ps) < 2:
        return None
    return round(float(np.mean([np.abs(small(a) - small(b)).mean() for a, b in itertools.combinations(ps, 2)])), 4)


SCORES_PATH = HERE / "scores.json"   # visual scores, filled by hand after looking at the sheets


def report():
    m = load()
    scores = json.loads(SCORES_PATH.read_text()) if SCORES_PATH.exists() else {}
    rows = [r | {"stem": st} | scores.get(st, {}) for st, r in m.items() if r.get("group") != "warm"]
    fields = ["stem", "group", "ver", "desc", "char", "scene", "action", "shape", "seed", "variation", "ref", "time_s",
              "border_white", "figures", "touches_edge", "bbox_h", "usable", "pieces_before", "hole_ratio", "gaps",
              "small_regions", "ink_ratio", "consistency", "content_ok", "people_animals", "note", "prompt"]
    with (HERE / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})
    # characters
    crs = [r for r in rows if r["group"] == "char"]
    if crs:
        for vl in VARS:
            rs = [r for r in crs if r["ver"] == vl]
            if not rs:
                continue
            divs = {d: diversity([OUT / f"char__{vl}__{d}__s{s}.png" for s in CHAR_SEEDS]) for d in DESCS}
            us = [r.get("usable") for r in rs if r.get("usable") is not None]
            print(f"{vl} n={len(rs)} border_white={np.mean([r['border_white'] for r in rs]):.3f} "
                  f"edge={sum(r['touches_edge'] for r in rs)} time={np.mean([r['time_s'] for r in rs]):.1f} "
                  f"usable={sum(us)}/{len(us)} div={divs}")
        # spike baselines (no variation / QENV) for the same diversity measure
        for v in ["QEN", "QZM", "QENV", "QZHV"]:
            print("spike", v, {d: diversity([SPIKE / f"char__{v}__{d}__s{s}.png" for s in CHAR_SEEDS])
                               for d in ["bunny", "girl", "dino", "astro"]})
        for vl in VARS:
            if any(r["ver"] == vl for r in crs):
                grid([(d, [OUT / f"char__{vl}__{d}__s{s}.png" for s in CHAR_SEEDS]) for d in DESCS],
                     [f"{vl} s{s}" for s in CHAR_SEEDS], f"sheet_characters_{vl}.jpg", cw=180, ch=262, lw=55)
    # poses
    prs = [r for r in rows if r["group"] == "pose"]
    if prs:
        chars = ["bunny51", "bunnyP6", *PICKS]
        grid([(ch, [ref_path(ch)] + [OUT / f"pose__{ch}__{ak}__EN__s101.png" for ak in E1_ACTIONS]) for ch in chars],
             ["reference", "wave EN", "jump EN"], "sheet_poses_E1.jpg", cw=200, ch=292, lw=60)
        for ch, shapes in [*((c, ("EN", "ZM", "ZH")) for c in E2_POSE_CHARS), ("bunnyP6", ("EN",))]:
            for shape in shapes:
                grid([(ak, [ref_path(ch)] + [OUT / f"pose__{ch}__{ak}__{shape}__s{s}.png" for s in POSE_SEEDS])
                      for ak in ACTIONS], ["reference", "s101", "s202"], f"sheet_poses_E2_{ch}_{shape}.jpg",
                     cw=170, ch=248, lw=50)
        print("pose summary (char, shape): n, time, gaps, small, cons, content, holes>5%")
        groups: dict = {}
        for r in prs:
            groups.setdefault((r["char"], r["shape"]), []).append(r)
        for g, rs in sorted(groups.items()):
            cs = [r["consistency"] for r in rs if r.get("consistency") is not None]
            co = [r["content_ok"] for r in rs if r.get("content_ok") is not None]
            print(g, len(rs), round(np.mean([r["time_s"] for r in rs]), 1),
                  round(np.mean([r["gaps"] for r in rs]), 1), round(np.mean([r["small_regions"] for r in rs]), 1),
                  round(np.mean(cs), 2) if cs else None, f"{sum(co)}/{len(co)}" if co else None,
                  sum(r["hole_ratio"] > 0.05 for r in rs))
    # backgrounds: EN (phase 8 VB) / ZH in EN template (spike ZB) / full ZH template (ZHT)
    brs = [r for r in rows if r["group"] == "bg"]
    if brs:
        cols = ["EN 101", "EN 202", "ZM 101", "ZM 202", "ZH 101", "ZH 202"]

        def bgp(src, k, s, la=""):
            return {"EN": P8 / f"VB__{k}__s{s}{la}.png", "ZM": SPIKE / f"bg__ZB__{k}__s{s}{la}.png",
                    "ZH": OUT / f"bg__ZHT__{k}__s{s}{la}.png"}[src]
        grid([(k, [bgp(src, k, s) for src in ("EN", "ZM", "ZH") for s in BG_SEEDS]) for k in SCENES_ZH], cols,
             "sheet_backgrounds.jpg", cw=170, ch=240, lw=80)
        grid([(k, [bgp(src, k, s, "_lineart") for src in ("EN", "ZM", "ZH") for s in BG_SEEDS]) for k in SCENES_ZH],
             cols, "sheet_backgrounds_lineart.jpg", cw=170, ch=240, lw=80)
        p8 = json.loads((P8 / "metrics.json").read_text())
        sp = json.loads((SPIKE / "metrics.json").read_text())
        for name, rs in [("EN VB (phase 8)", [r for r in p8.values() if r.get("variant") == "VB"]),
                         ("ZM ZB (spike)", [r for r in sp.values() if r.get("ver") == "ZB"]),
                         ("ZH ZHT", [r for r in brs if r["ver"] == "ZHT"])]:
            print(f"{name}: n={len(rs)} gaps={np.mean([r['gaps'] for r in rs]):.1f} "
                  f"small={np.mean([r['small_regions'] for r in rs]):.1f} time={np.mean([r['time_s'] for r in rs]):.1f}")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1]
    if cmd == "char":
        gen_char(sys.argv[2:] or list(VARS))
    elif cmd == "pose":
        gen_pose()
    elif cmd == "bg":
        gen_bg()
    elif cmd == "free":
        free()
    elif cmd == "report":
        report()
