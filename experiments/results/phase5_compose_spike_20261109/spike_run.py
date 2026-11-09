"""Spike：背景單獨生成 → 角色新動作（白底）→ 去背 → 合成 → 線稿。拋棄式實驗，不進產品。"""
import copy, io, json, sys, time, uuid
from pathlib import Path
import httpx
from PIL import Image
sys.path.insert(0, "/ai-data/repos/coloring-books/backend")
from app.services.lineart_render import render_base, LineDetail, ink_to_png
from app.services.qa import evaluate
import numpy as np

C = "http://127.0.0.1:8188"; OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
REPO = Path("/ai-data/repos/coloring-books")
WF = json.loads((REPO / "comfy_workflows/route_a_sdxl_ipadapter_cn/workflow_api.json").read_text())
CHAR = Path("/ai-data/temp/ubuntu24.04/claude-1000/-ai-data-repos-coloring-books/0571fc5b-d4ca-4525-ba7b-da0150cd1ebc/scratchpad/uicheck/storage/books/1/generations/1.png")
DESC = "a cute small bunny girl with long upright blue bunny ears, blue bob hair, big round eyes, plain blue overalls, blue shoes"
STYLE = "children's picture book illustration, flat colors, thick clean black outlines, simple shapes"
BG_NEG = "people, person, character, girl, boy, child, animal, rabbit, photo, realistic, 3d render, gradient, soft shading, texture, noise, blurry, text, watermark"
POSE_NEG = "colored background, scenery, landscape, ground, multiple views, character sheet, cropped, photo, realistic, 3d render, gradient, soft shading, texture, noise, blurry, text, watermark, multiple characters"
SCENES = [
  ("park", "a sunny park playground with a swing set, trees and flowers", "park_swing_props.png", "sitting on a swing seat, holding the swing ropes with both hands, happy"),
  ("kitchen", "a cozy kitchen with cupboards, an oven, a table with a bowl and cookies", "kitchen_baking_props.png", "standing and holding a tray of cookies with both hands, smiling"),
  ("bedroom", "a cozy bedroom with a bed, a bookshelf and a window", "bedroom_reading_props.png", "sitting on the floor reading an open book, cross-legged"),
]
cli = httpx.Client(timeout=120)
def upload(p):
    r = cli.post(f"{C}/upload/image", files={"image": (f"spike_{uuid.uuid4().hex[:8]}.png", Path(p).read_bytes(), "image/png")}, data={"overwrite": "true"}); r.raise_for_status(); return r.json()["name"]
def run(wf):
    pid = cli.post(f"{C}/prompt", json={"prompt": wf}).json()["prompt_id"]
    while True:
        h = cli.get(f"{C}/history/{pid}").json()
        if pid in h:
            if h[pid]["status"].get("status_str") == "error": raise RuntimeError(json.dumps(h[pid]["status"])[:500])
            for o in h[pid]["outputs"].values():
                for im in o.get("images", []):
                    return Image.open(io.BytesIO(cli.get(f"{C}/view", params=im).content)).convert("RGB")
        time.sleep(0.5)
def gen(prompt, neg, seed, w=864, h=1216, char=None, ip=0.55, ip_end=0.6, sketch=None, cn=0.0):
    wf = copy.deepcopy(WF)
    wf["2"]["inputs"]["text"] = prompt; wf["3"]["inputs"]["text"] = neg
    wf["13"]["inputs"].update(width=w, height=h); wf["14"]["inputs"]["seed"] = seed
    if char:
        wf["4"]["inputs"]["image"] = char; wf["7"]["inputs"].update(weight=ip, end_at=ip_end)
    else:  # 不用 IP-Adapter：KSampler 直接吃 checkpoint model
        wf["14"]["inputs"]["model"] = ["1", 0]
        for k in ("4", "5", "6", "7"): wf.pop(k)
    if sketch:
        wf["8"]["inputs"]["image"] = sketch; wf["12"]["inputs"]["strength"] = cn
    else:
        wf["14"]["inputs"]["positive"] = ["2", 0]; wf["14"]["inputs"]["negative"] = ["3", 0]
        for k in ("8", "9", "10", "11", "12"): wf.pop(k)
    return run(wf)
def cutout(img_path):
    name = upload(img_path)
    wf = {"1": {"class_type": "LoadImage", "inputs": {"image": name}},
          "2": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}},
          "3": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
          "4": {"class_type": "MaskToImage", "inputs": {"mask": ["3", 0]}},
          "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "spike_mask"}}}
    return run(wf).convert("L")
char_name = upload(CHAR)
log = []
for key, scene, props, action in SCENES:
    t = time.time()
    bg_text = gen(f"{scene}, {STYLE}, full detailed background scene, empty scene with no people", BG_NEG, 101); bg_text.save(OUT / f"{key}_bg_text.png")
    sk = upload(REPO / "experiments/samples/pose" / props)
    bg_sk = gen(f"{scene}, {STYLE}, full detailed background scene, empty scene with no people", BG_NEG, 101, sketch=sk, cn=0.5); bg_sk.save(OUT / f"{key}_bg_sketch.png")
    for ip, end in ((0.55, 0.6), (0.8, 0.8)):
        p = gen(f"{DESC}, {action}, full body, {STYLE}, (isolated on pure white background:1.3), centered", POSE_NEG, 202, w=832, h=1216, char=char_name, ip=ip, ip_end=end)
        p.save(OUT / f"{key}_pose_ip{ip}.png")
    log.append((key, round(time.time() - t, 1)))
    print(key, "gen done", log[-1][1], "s", flush=True)
# 去背 + 合成 + 線稿（用 IP 0.8 的動作圖、文字背景）
for key, *_ in SCENES:
    pose = Image.open(OUT / f"{key}_pose_ip0.8.png"); mask = cutout(OUT / f"{key}_pose_ip0.8.png")
    mask.save(OUT / f"{key}_mask.png")
    bbox = mask.point(lambda v: 255 if v > 128 else 0).getbbox()
    pc, mc = pose.crop(bbox), mask.crop(bbox)
    bg = Image.open(OUT / f"{key}_bg_text.png").convert("RGB")
    H = int(bg.height * 0.6); W = int(pc.width * H / pc.height)
    pc, mc = pc.resize((W, H), Image.LANCZOS), mc.resize((W, H), Image.LANCZOS)
    comp = bg.copy(); comp.paste(pc, ((bg.width - W) // 2, bg.height - H - int(bg.height * 0.04)), mc)
    comp.save(OUT / f"{key}_composite.png")
    ink, _ = render_base(np.asarray(comp), (2244, 3272), LineDetail.STANDARD, 11)
    (OUT / f"{key}_lineart.png").write_bytes(ink_to_png(ink))
    print(key, "composite gaps", evaluate(ink, 11).gap_count, flush=True)
