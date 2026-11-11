"""Vehicles series spike (throwaway): can qwen_edit_pose_cutout draw "character + vehicle" cleanly?

Questions:
  Q1 Does the production pose template T2 ("draw only the character … no background objects") drop the vehicle?
  Q2 Does a vehicle-aware template TV keep a recognizable, simple vehicle with acceptable line art (gaps, tiny regions)?
  Q3 Portrait (production 832×1216) vs landscape (1216×832) for character + vehicle.
  Q4 Do vehicle scenes work as Qwen backgrounds (no vehicle drawn in them, few tiny regions)?
Refs: phase 9 VG bunny / girl (copied into processed/ref_*.png). Actions are Chinese inside the English template (ZM).

Usage (repo root):
  PY="env -u PYTHONPATH PYTHONPATH=backend:. /ai-data/venvs/ubuntu24.04/coloring-books/bin/python"
  $PY experiments/results/phase11_vehicles_spike_20261111/run.py pose|bg|free|report
"""
import importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
P9 = HERE.parent / "phase9_all_qwen_20261110_1700" / "run.py"
spec = importlib.util.spec_from_file_location("p9", P9)
p9 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p9)
# redirect the phase 9 helpers' output folders to this experiment
p9.OUT = HERE / "processed"
p9.SHEETS = HERE / "sheets"
p9.MPATH = p9.OUT / "metrics.json"

from app.services.prompts import QWEN_POSE_TEMPLATE, qwen_background_prompt  # noqa: E402

T2 = QWEN_POSE_TEMPLATE
TV = ("Make this exact character {action}. Draw the character together with the vehicle the action mentions; the "
      "character and the whole vehicle are both completely visible. The vehicle is simple and cute, chunky rounded "
      "shapes, few details, big simple wheels and windows. No ground, no floor, no road, no scenery, no other "
      "background objects. Keep the same character design, proportions, colors, clothing and accessories unchanged. "
      "Centered, plain pure white background, no shadow, no cast shadow, children's picture book illustration, flat "
      "colors, thick clean black outlines.")
VEHICLES = {  # key -> (Chinese action as a user would type it)
    "fire": "開著消防車，從車窗探出頭揮手",
    "ambulance": "站在救護車旁邊揮手",
    "police": "坐在警車的駕駛座上",
    "bus": "坐在公車裡，從窗戶往外看",
    "train": "坐在火車頭上開心地笑",
    "plane": "坐在小飛機的駕駛座上",
    "boat": "坐在小帆船上划船",
    "bike": "騎著腳踏車",
}
PORTRAIT, LANDSCAPE = (832, 1216), (1216, 832)
# (stem, char, vehicle, template, seed, size)
JOBS = []
for v in VEHICLES:
    JOBS.append((f"bunny__{v}__T2__s101", "bunny", v, "T2", 101, PORTRAIT))
    for s in (101, 202):
        JOBS.append((f"bunny__{v}__TV__s{s}", "bunny", v, "TV", s, PORTRAIT))
    JOBS.append((f"bunny__{v}__TVL__s101", "bunny", v, "TVL", 101, LANDSCAPE))
    JOBS.append((f"girl__{v}__TV__s101", "girl", v, "TV", 101, PORTRAIT))
SCENES = {
    "fire": "消防局門口，有大車庫門和消防栓",
    "hospital": "醫院門口，有大門和招牌上的紅十字",
    "busstop": "公車站，有站牌和候車亭",
    "station": "火車站月台，有鐵軌和時鐘",
    "airport": "機場，有塔台和跑道",
    "harbor": "港口，有碼頭和燈塔",
    "road": "城市馬路，有紅綠燈和斑馬線",
    "park": "公園的腳踏車道，有大樹和長椅",
}


def gen_pose():
    m = p9.load()
    entry = p9.REG.get("qwen_edit_pose_cutout")
    p9.gpu_idle_wait()
    up = {}
    order = sorted(JOBS, key=lambda j: (j[3] != "TV", j[1] != "bunny", j[0]))  # TV bunny first
    for stem, ch, v, t, s, size in order:
        if stem in m:
            continue
        if ch not in up:
            up[ch] = p9.upload(p9.OUT / f"ref_{ch}.png")
        p = (T2 if t == "T2" else TV).format(action=VEHICLES[v])
        vals = {"prompt": p, "seed": s, "width": size[0], "height": size[1], "character_image": up[ch]}
        outs, dt = p9.run(entry.build_prompt(vals).prompt)
        img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
        img.save(p9.OUT / f"{stem}.png")
        mask.save(p9.OUT / f"{stem}_mask.png")
        m[stem] = {"group": "pose", "char": ch, "vehicle": v, "tpl": t, "seed": s, "size": list(size), "time_s": dt,
                   **p9.pose_metrics(img, mask, stem), "prompt": p}
        p9.save(m)
        print(stem, {k: val for k, val in m[stem].items() if k != "prompt"}, flush=True)


def gen_bg():
    m = p9.load()
    entry = p9.REG.get("qwen_background")
    p9.gpu_idle_wait()
    for k, sc in SCENES.items():
        stem = f"bg__{k}__s101"
        if stem in m:
            continue
        p = qwen_background_prompt(sc)
        outs, dt = p9.run(entry.build_prompt({"prompt": p, "seed": 101, "width": p9.BG_SIZE[0],
                                              "height": p9.BG_SIZE[1]}).prompt)
        img = outs["OUTPUT"].convert("RGB")
        img.save(p9.OUT / f"{stem}.png")
        m[stem] = {"group": "bg", "scene": k, "time_s": dt, **p9.line_metrics(img, stem), "prompt": p}
        p9.save(m)
        print(stem, {k2: v for k2, v in m[stem].items() if k2 != "prompt"}, flush=True)


def report():
    O = p9.OUT
    rows = [(v, [O / f"bunny__{v}__T2__s101_cut.png", O / f"bunny__{v}__TV__s101_cut.png",
                 O / f"bunny__{v}__TV__s202_cut.png", O / f"girl__{v}__TV__s101_cut.png"]) for v in VEHICLES]
    p9.grid(rows, ["T2 (prod)", "TV s101", "TV s202", "girl TV"], "sheet_poses_portrait.jpg", cw=200, ch=292, lw=80)
    rows = [(v, [O / f"bunny__{v}__TVL__s101_cut.png", O / f"bunny__{v}__TV__s101_cut.png"]) for v in VEHICLES]
    p9.grid(rows, ["TVL landscape", "TV portrait"], "sheet_poses_landscape.jpg", cw=300, ch=292, lw=80)
    rows = [(k, [O / f"bg__{k}__s101.png", O / f"bg__{k}__s101_lineart.png"]) for k in SCENES]
    p9.grid(rows, ["background", "lineart"], "sheet_backgrounds.jpg", cw=220, ch=310, lw=80)
    m = p9.load()
    import statistics as st
    print("group  n  gaps(mean)  small_regions(mean)  pieces(mean)  time(mean)")
    for t in ("T2", "TV", "TVL"):
        r = [v for v in m.values() if v.get("group") == "pose" and v["tpl"] == t]
        if r:
            print(t, len(r), round(st.mean(x["gaps"] for x in r), 1), round(st.mean(x["small_regions"] for x in r), 1),
                  round(st.mean(x["pieces_before"] for x in r), 1), round(st.mean(x["time_s"] for x in r), 1))
    r = [v for v in m.values() if v.get("group") == "bg"]
    if r:
        print("bg", len(r), round(st.mean(x["gaps"] for x in r), 1), round(st.mean(x["small_regions"] for x in r), 1),
              "-", round(st.mean(x["time_s"] for x in r), 1))


# ---- round 2: T3 = production T2 minimally widened to allow the vehicle/object the action mentions ----
T3 = ("Make this exact character {action}. Draw only the character, plus the vehicle or object the action mentions, "
      "completely visible; no ground, no floor, no road, no scenery, no room, no other background objects. Keep the "
      "same character design, proportions, colors, clothing and accessories unchanged. Full body, centered, plain pure "
      "white background, no shadow, no cast shadow, children's picture book illustration, flat colors, thick clean "
      "black outlines.")
VEHICLES2 = {**VEHICLES, "police_x": "坐在白色和藍色的警車駕駛座上，車頂有紅藍警燈"}
REGULAR = {k: zh for k, (en, zh) in p9.ACTIONS.items()}  # sit/hold/run/wave/jump/read in Chinese
NO_VEHICLE = "，沒有任何車輛、船、飛機或火車"
SCENES2 = ["fire", "harbor", "airport", "station"]


def gen_round2():
    m = p9.load()
    entry = p9.REG.get("qwen_edit_pose_cutout")
    p9.gpu_idle_wait()
    ref = p9.upload(p9.OUT / "ref_bunny.png")
    jobs = [(f"r2__bunny__{v}__T3__s{s}", a, s) for v, a in VEHICLES2.items() for s in (101, 202)]
    jobs += [(f"r2__bunny__act_{k}__T3__s101", a, 101) for k, a in REGULAR.items()]
    for stem, action, s in jobs:
        if stem in m:
            continue
        p = T3.format(action=action)
        vals = {"prompt": p, "seed": s, "width": PORTRAIT[0], "height": PORTRAIT[1], "character_image": ref}
        outs, dt = p9.run(entry.build_prompt(vals).prompt)
        img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
        img.save(p9.OUT / f"{stem}.png")
        mask.save(p9.OUT / f"{stem}_mask.png")
        m[stem] = {"group": "r2pose", "action": action, "seed": s, "time_s": dt,
                   **p9.pose_metrics(img, mask, stem), "prompt": p}
        p9.save(m)
        print(stem, {k: val for k, val in m[stem].items() if k != "prompt"}, flush=True)
    bg = p9.REG.get("qwen_background")
    for k in SCENES2:
        stem = f"r2__bg__{k}__novehicle__s101"
        if stem in m:
            continue
        p = qwen_background_prompt(SCENES[k] + NO_VEHICLE)
        outs, dt = p9.run(bg.build_prompt({"prompt": p, "seed": 101, "width": p9.BG_SIZE[0],
                                           "height": p9.BG_SIZE[1]}).prompt)
        img = outs["OUTPUT"].convert("RGB")
        img.save(p9.OUT / f"{stem}.png")
        m[stem] = {"group": "r2bg", "scene": k, "time_s": dt, **p9.line_metrics(img, stem), "prompt": p}
        p9.save(m)
        print(stem, {k2: v for k2, v in m[stem].items() if k2 != "prompt"}, flush=True)


def report2():
    O = p9.OUT
    rows = [(v, [O / (f"bunny__{v}__T2__s101_cut.png" if v in VEHICLES else "none"),
                 O / f"r2__bunny__{v}__T3__s101_cut.png", O / f"r2__bunny__{v}__T3__s202_cut.png"]) for v in VEHICLES2]
    p9.grid(rows, ["T2 (prod)", "T3 s101", "T3 s202"], "sheet_r2_vehicles.jpg", cw=200, ch=292, lw=80)
    rows = [(k, [O / f"r2__bunny__act_{k}__T3__s101_cut.png"]) for k in REGULAR]
    p9.grid(rows, ["T3 regular actions"], "sheet_r2_regular.jpg", cw=200, ch=292, lw=60)
    rows = [(k, [O / f"bg__{k}__s101.png", O / f"r2__bg__{k}__novehicle__s101.png",
                 O / f"r2__bg__{k}__novehicle__s101_lineart.png"]) for k in SCENES2]
    p9.grid(rows, ["as is", "no vehicle", "lineart"], "sheet_r2_backgrounds.jpg", cw=220, ch=310, lw=80)
    import statistics as st
    m = p9.load()
    for g in ("r2pose", "r2bg"):
        r = [v for v in m.values() if v.get("group") == g]
        if r:
            print(g, len(r), "gaps", round(st.mean(x["gaps"] for x in r), 1), "small",
                  round(st.mean(x["small_regions"] for x in r), 1), "time", round(st.mean(x["time_s"] for x in r), 1))


if __name__ == "__main__":
    {"pose": gen_pose, "bg": gen_bg, "free": p9.free, "report": report, "round2": gen_round2, "report2": report2}[sys.argv[1]]()
