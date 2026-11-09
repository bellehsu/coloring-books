"""Spike：角色動作一致性比較（BASELINE / QWEN / OPENPOSE）。拋棄式實驗，不進產品。

用法（在 repo 根目錄）：
  env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python \
      experiments/results/phase5_pose_consistency_20261110_0739/run.py gen BASELINE [OPENPOSE QWEN ...]
  ... run.py sheets   # 拼總覽圖
  ... run.py csv      # 指標 + 下方 SCORES（人工評分）→ results.csv

候選名稱可加變體：OPENPOSE（cn 0.7）、OPENPOSE_cn0.6、OPENPOSE_cn0.8、QWEN（GGUF）、QWEN_fp8。
所有節點與輸入都已用 ComfyUI 0.36.0 @ 8188 的 /object_info 驗證（見 NOTES.md）。
"""
import copy, csv, io, json, math, subprocess, sys, time, uuid
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path("/ai-data/repos/coloring-books")
sys.path.insert(0, str(REPO / "backend"))
from app.services.lineart_render import LineDetail, ink_to_png, render_base  # noqa: E402
from app.services.prompts import POSE_NEGATIVE, POSE_SIZE, pose_prompt  # noqa: E402
from app.services.qa import evaluate  # noqa: E402

C = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
OUT = HERE / "processed"
REF = Path("/ai-data/temp/ubuntu24.04/claude-1000/-ai-data-repos-coloring-books/"
           "0571fc5b-d4ca-4525-ba7b-da0150cd1ebc/scratchpad/p5acc/storage/books/1/generations/1.png")
DESC = "a small round white bunny with long ears, wearing a red scarf"  # book 1 的 character_description
ACTIONS = ["waving one hand", "holding something with both hands", "jumping happily", "running", "sitting",
           "reading a book"]
SEEDS = [101, 202]
W, H = POSE_SIZE  # 832×1216
CONTENT_PX = (2244, 3272)  # A4 300 DPI 內容區（與 compose spike 相同）
LINE_W = 11
QWEN_PROMPT = ("Make this exact character {action}. Keep the same character design, proportions, colors, scarf and "
               "ears unchanged. Full body, centered, plain pure white background, children's picture book "
               "illustration, flat colors, thick clean black outlines.")

cli = httpx.Client(timeout=600)


def slug(a: str) -> str:
    return a.replace(" ", "_")


# ---------------------------------------------------------------- ComfyUI
def upload(p: Path) -> str:
    r = cli.post(f"{C}/upload/image", files={"image": (f"posecmp_{uuid.uuid4().hex[:8]}.png", p.read_bytes(),
                                                       "image/png")}, data={"overwrite": "true"})
    r.raise_for_status()
    return r.json()["name"]


def run(wf: dict) -> dict[str, Image.Image]:
    """送出 prompt，回傳 {SaveImage 的 _meta.title: 圖}。"""
    res = cli.post(f"{C}/prompt", json={"prompt": wf})
    if res.status_code != 200:
        raise RuntimeError(res.text[:800])
    pid = res.json()["prompt_id"]
    while True:
        h = cli.get(f"{C}/history/{pid}").json()
        if pid in h:
            st = h[pid]["status"]
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                raise RuntimeError(json.dumps(msgs)[:1500])
            out = {}
            for nid, o in h[pid]["outputs"].items():
                for im in o.get("images", []):
                    out[wf[nid]["_meta"]["title"]] = Image.open(io.BytesIO(cli.get(f"{C}/view", params=im).content))
            return out
        time.sleep(0.5)


def gpu_idle_wait():
    """GPU 被其他工作占用（util >10% 連續三次或佇列非空）就等。"""
    while True:
        busy = 0
        for _ in range(3):
            u = int(subprocess.check_output(["nvidia-smi", "--query-gpu=utilization.gpu",
                                             "--format=csv,noheader,nounits"]).decode().strip().split()[0])
            q = cli.get(f"{C}/queue").json()
            if u > 10 or q["queue_running"] or q["queue_pending"]:
                busy += 1
            time.sleep(2)
        if busy < 3:
            return
        print("GPU busy, waiting 60 s", flush=True)
        time.sleep(60)


def _meta(t):
    return {"_meta": {"title": t}}


def bg_nodes(image_ref) -> dict:
    """與 sdxl_pose_cutout 相同的 BiRefNet 去背節點。"""
    return {
        "17": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"},
               **_meta("BG_REMOVAL_MODEL")},
        "18": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["17", 0], "image": image_ref},
               **_meta("REMOVE_BG")},
        "19": {"class_type": "MaskToImage", "inputs": {"mask": ["18", 0]}, **_meta("MASK_TO_IMAGE")},
        "20": {"class_type": "SaveImage", "inputs": {"images": ["19", 0], "filename_prefix": "posecmp/mask"},
               **_meta("MASK")},
    }


POSE_WF = json.loads((REPO / "comfy_workflows/sdxl_pose_cutout/workflow_api.json").read_text())


def wf_baseline(ref: str, action: str, seed: int) -> dict:
    """產品現行 sdxl_pose_cutout：manifest 預設值 + comfy_engine 傳入的 IP 0.55 / end 0.6。"""
    wf = copy.deepcopy(POSE_WF)
    wf["2"]["inputs"]["text"] = pose_prompt(DESC, action)
    wf["3"]["inputs"]["text"] = POSE_NEGATIVE
    wf["4"]["inputs"]["image"] = ref
    wf["7"]["inputs"].update(weight=0.55, end_at=0.6)
    wf["13"]["inputs"].update(width=W, height=H)
    wf["14"]["inputs"]["seed"] = seed
    wf["16"]["inputs"]["filename_prefix"] = "posecmp/baseline"
    return wf


def wf_openpose(ref: str, skel: str, action: str, seed: int, cn: float) -> dict:
    """baseline + IP 0.8/end 1.0 + ControlNet Union promax（openpose）。"""
    wf = wf_baseline(ref, action, seed)
    wf["7"]["inputs"].update(weight=0.8, end_at=1.0)
    wf.update({
        "8": {"class_type": "LoadImage", "inputs": {"image": skel}, **_meta("POSE_IMAGE")},
        "10": {"class_type": "ControlNetLoader",
               "inputs": {"control_net_name": "controlnet-union-sdxl-1.0-promax.safetensors"}, **_meta("CONTROLNET")},
        "11": {"class_type": "SetUnionControlNetType", "inputs": {"control_net": ["10", 0], "type": "openpose"},
               **_meta("UNION_TYPE")},
        "12": {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": ["2", 0], "negative": ["3", 0], "control_net": ["11", 0], "image": ["8", 0],
                          "vae": ["1", 2], "strength": cn, "start_percent": 0.0, "end_percent": 1.0},
               **_meta("APPLY_CONTROLNET")},
    })
    wf["14"]["inputs"]["positive"] = ["12", 0]
    wf["14"]["inputs"]["negative"] = ["12", 1]
    wf["16"]["inputs"]["filename_prefix"] = "posecmp/openpose"
    return wf


def wf_qwen(ref: str, action: str, seed: int, fp8: bool) -> dict:
    """Qwen-Image-Edit-2511 + Lightning 4-step；結構照內建範本 image_qwen_image_edit_2511 的子圖。"""
    unet = ({"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_edit_2511_fp8mixed.safetensors",
                                                    "weight_dtype": "default"}} if fp8 else
            {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "qwen-image-edit-2511-Q4_K_M.gguf"}})
    wf = {
        "1": {**unet, **_meta("UNET")},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                                                     "type": "qwen_image", "device": "default"},
              **_meta("TEXT_ENCODER")},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}, **_meta("VAE")},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1},
              **_meta("MODEL_SAMPLING")},
        "5": {"class_type": "CFGNorm", "inputs": {"model": ["4", 0], "strength": 1.0}, **_meta("CFG_NORM")},
        "6": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["5", 0], "lora_name": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
                         "strength_model": 1.0}, **_meta("LIGHTNING_LORA")},
        "7": {"class_type": "LoadImage", "inputs": {"image": ref}, **_meta("CHARACTER_REF")},
        "8": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "prompt": QWEN_PROMPT.format(action=action), "vae": ["3", 0],
                         "image1": ["7", 0]}, **_meta("POSITIVE")},
        "9": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "prompt": "", "vae": ["3", 0], "image1": ["7", 0]}, **_meta("NEGATIVE")},
        "10": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["8", 0], "reference_latents_method": "index_timestep_zero"},
               **_meta("REF_METHOD_POS")},
        "11": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["9", 0], "reference_latents_method": "index_timestep_zero"},
               **_meta("REF_METHOD_NEG")},
        "12": {"class_type": "EmptySD3LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1},
               **_meta("LATENT")},
        "13": {"class_type": "KSampler",
               "inputs": {"model": ["6", 0], "positive": ["10", 0], "negative": ["11", 0], "latent_image": ["12", 0],
                          "seed": seed, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                          "denoise": 1.0}, **_meta("SAMPLER")},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}, **_meta("DECODE")},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["14", 0], "filename_prefix": "posecmp/qwen"},
               **_meta("OUTPUT")},
    }
    return wf


# ---------------------------------------------------------------- OpenPose skeletons（COCO-18，controlnet_aux 配色）
LIMB_SEQ = [(2, 3), (2, 6), (3, 4), (4, 5), (6, 7), (7, 8), (2, 9), (9, 10), (10, 11), (2, 12), (12, 13), (13, 14),
            (2, 1), (1, 15), (15, 17), (1, 16), (16, 18)]
COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0),
          (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255),
          (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85)]
STICK, RADIUS = 9, 9  # controlnet_aux 在 512 偵測解析度用 4，放大到 1216 高約 9 px


def _base_pose() -> dict[int, tuple[float, float]]:
    """站姿，矮胖大頭（照參考圖量：兩眼距 ~170 px、鼻 y≈470、腳 y≈1070）；腿短。索引為 OpenPose COCO-18（0 起算），R = 畫面左側。"""
    return {0: (416, 475), 1: (416, 590), 2: (340, 625), 3: (305, 715), 4: (300, 800), 5: (492, 625),
            6: (527, 715), 7: (532, 800), 8: (370, 860), 9: (365, 965), 10: (362, 1065), 11: (462, 860),
            12: (467, 965), 13: (470, 1065), 14: (330, 420), 15: (502, 420), 16: (255, 450), 17: (577, 450)}


def _shift(p, dy=0, dx=0, keys=None):
    return {k: (x + dx, y + dy) if keys is None or k in keys else (x, y) for k, (x, y) in p.items()}


HEAD = (0, 14, 15, 16, 17)


def pose_points(action: str) -> dict[int, tuple[float, float]]:
    p = _base_pose()
    if action == "waving one hand":  # 右手（畫面左）舉高揮手
        p.update({3: (250, 565), 4: (190, 430)})
    elif action == "holding something with both hands":  # 雙手在胸前
        p.update({3: (335, 730), 4: (385, 700), 6: (497, 730), 7: (447, 700)})
    elif action == "jumping happily":  # 雙手 V 字上舉、屈膝、整體上移
        p.update({3: (270, 540), 4: (205, 420), 6: (562, 540), 7: (627, 420),
                  9: (330, 930), 10: (350, 1010), 12: (502, 930), 13: (482, 1010)})
        p = _shift(p, dy=-70)
    elif action == "running":  # 前後擺臂、跨步（偏正面）
        p.update({3: (290, 650), 4: (310, 560), 6: (545, 700), 7: (590, 650),
                  9: (330, 940), 10: (300, 1010), 12: (500, 975), 13: (545, 1075)})
    elif action in ("sitting", "reading a book"):  # 坐地、腿往前伸開
        p = _shift(p, dy=110, keys=(0, 1, 2, 3, 4, 5, 6, 7, 14, 15, 16, 17))
        p.update({8: (370, 960), 11: (462, 960), 9: (320, 1030), 10: (290, 1100), 12: (512, 1030),
                  13: (542, 1100)})
        if action == "sitting":
            p.update({3: (300, 820), 4: (320, 910), 6: (532, 820), 7: (512, 910)})
        else:  # 雙手捧書在胸前、頭微低
            p.update({3: (330, 840), 4: (370, 790), 6: (502, 840), 7: (462, 790)})
            p = _shift(p, dy=20, keys=HEAD)
    return p


def draw_skeleton(action: str) -> Image.Image:
    p = pose_points(action)
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, (a, b) in enumerate(LIMB_SEQ):
        (x1, y1), (x2, y2) = p[a - 1], p[b - 1]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        length = math.hypot(x2 - x1, y2 - y1) / 2
        ang = math.atan2(y2 - y1, x2 - x1)
        poly = [(mx + length * math.cos(t) * math.cos(ang) - STICK * math.sin(t) * math.sin(ang),
                 my + length * math.cos(t) * math.sin(ang) + STICK * math.sin(t) * math.cos(ang))
                for t in np.linspace(0, 2 * math.pi, 72, endpoint=False)]
        d.polygon(poly, fill=tuple(int(c * 0.6) for c in COLORS[i]))
    for i in range(18):
        x, y = p[i]
        d.ellipse((x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS), fill=COLORS[i])
    return img


# ---------------------------------------------------------------- 指標
def analyse(img: Image.Image, mask: Image.Image, stem: str) -> dict:
    rgb = np.asarray(img.convert("RGB"), np.float32)
    m = np.asarray(mask.convert("L"), np.float32) / 255.0
    binm = (m > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binm, connectivity=8)
    areas = sorted(stats[1:, cv2.CC_STAT_AREA].tolist(), reverse=True)
    total = int(binm.sum())
    main_share = areas[0] / total if total else 0.0
    big = sum(1 for a in areas if a >= 0.005 * total)  # 佔遮罩 ≥0.5% 的塊
    ratio = total / binm.size
    mask_ok = bool(total and main_share >= 0.98 and big == 1 and 0.05 <= ratio <= 0.8)
    cut = (rgb * m[..., None] + 255.0 * (1 - m[..., None])).clip(0, 255).astype(np.uint8)
    Image.fromarray(cut).save(OUT / f"{stem}_cut.png")
    ink, _ = render_base(cut, CONTENT_PX, LineDetail.STANDARD, LINE_W)
    (OUT / f"{stem}_lineart.png").write_bytes(ink_to_png(ink))
    rep = evaluate(ink, LINE_W)
    return {"mask_ok": mask_ok, "mask_area_ratio": round(ratio, 4), "mask_main_share": round(main_share, 4),
            "mask_big_components": big, "gaps": rep.gap_count,
            "small_regions": rep.metrics["tiny_region_count"], "fragments": rep.metrics["fragment_count"]}


# ---------------------------------------------------------------- 主流程
def gen(cands: list[str]):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "skeletons").mkdir(exist_ok=True)
    mpath = OUT / "metrics.json"
    metrics = json.loads(mpath.read_text()) if mpath.exists() else {}
    ref = upload(REF)
    skels = {}
    for a in ACTIONS:
        sp = OUT / "skeletons" / f"{slug(a)}.png"
        draw_skeleton(a).save(sp)
        skels[a] = sp
    for cand in cands:
        gpu_idle_wait()
        print("==", cand, flush=True)
        for a in ACTIONS:
            skel_ref = upload(skels[a]) if cand.startswith("OPENPOSE") else None
            for s in SEEDS:
                stem = f"{cand}_{slug(a)}_s{s}"
                if cand.startswith("BASELINE"):
                    wf = wf_baseline(ref, a, s)
                elif cand.startswith("OPENPOSE"):
                    cn = float(cand.split("_cn")[1]) if "_cn" in cand else 0.7
                    wf = wf_openpose(ref, skel_ref, a, s, cn)
                else:
                    wf = wf_qwen(ref, a, s, fp8=cand.endswith("fp8"))
                    wf.update(bg_nodes(["14", 0]))
                t = time.time()
                try:
                    outs = run(wf)
                except Exception as e:  # noqa: BLE001
                    metrics[stem] = {"candidate": cand, "action": a, "seed": s, "error": str(e)[:1500]}
                    print(stem, "ERROR", str(e)[:400], flush=True)
                    mpath.write_text(json.dumps(metrics, indent=1, ensure_ascii=False))
                    continue
                dt = round(time.time() - t, 1)
                img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
                img.save(OUT / f"{stem}.png")
                mask.save(OUT / f"{stem}_mask.png")
                rec = {"candidate": cand, "action": a, "seed": s, "time_s": dt, **analyse(img, mask, stem)}
                metrics[stem] = rec
                mpath.write_text(json.dumps(metrics, indent=1, ensure_ascii=False))
                print(stem, rec, flush=True)


# ---------------------------------------------------------------- 總覽圖
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)


def _thumb(p: Path, w: int, h: int) -> Image.Image:
    if not p.exists():
        im = Image.new("RGB", (w, h), (230, 200, 200))
        ImageDraw.Draw(im).text((10, h // 2), "missing / failed", fill=(0, 0, 0), font=FONT)
        return im
    im = Image.open(p).convert("RGB")
    im.thumbnail((w, h), Image.LANCZOS)
    cell = Image.new("RGB", (w, h), (255, 255, 255))
    cell.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return cell


def sheet(path: Path, cols: list[tuple[str, callable]], cw: int, ch: int):
    """rows = actions；第 0 欄：最上面是參考圖，其餘是動作名稱。"""
    lab = 26
    rows = len(ACTIONS) + 1
    img = Image.new("RGB", ((len(cols) + 1) * cw, rows * (ch + lab)), (255, 255, 255))
    d = ImageDraw.Draw(img)
    img.paste(_thumb(REF, cw, ch), (0, lab))
    d.text((4, 3), "REFERENCE", fill=(0, 0, 0), font=FONT)
    for j, (title, _) in enumerate(cols):
        d.text(((j + 1) * cw + 4, (ch + lab) - 30 + ch // 2), title, fill=(0, 0, 0), font=FONT)
    for i, a in enumerate(ACTIONS):
        y = (i + 1) * (ch + lab)
        d.text((4, y + ch // 2), a.replace("something ", "sth\n").replace(" with", "\nwith"), fill=(0, 0, 0),
               font=FONT)
        for j, (title, f) in enumerate(cols):
            d.text(((j + 1) * cw + 4, y + 3), title, fill=(90, 90, 90), font=FONT)
            img.paste(_thumb(f(a), cw, ch), ((j + 1) * cw, y + lab))
    img.save(path, quality=85)
    print(path, img.size)


def sheets():
    cw, ch = 200, 255  # 7 列 ×（255＋26）< 2000 px
    for cand in ("BASELINE", "QWEN", "OPENPOSE", "OPENPOSE_cn0.6", "OPENPOSE_cn0.8", "QWEN_fp8"):
        if not any(OUT.glob(f"{cand}_*_s101.png")):
            continue
        cols = [(f"s{s}", lambda a, s=s, c=cand: OUT / f"{c}_{slug(a)}_s{s}.png") for s in SEEDS]
        if cand.startswith("OPENPOSE"):
            cols = [("skeleton", lambda a: OUT / "skeletons" / f"{slug(a)}.png")] + cols
        sheet(OUT / f"sheet_{cand}.jpg", cols, cw, ch)
    sheet(OUT / "sheet_combined.jpg",
          [(c, lambda a, c=c: OUT / f"{c}_{slug(a)}_s101.png") for c in ("BASELINE", "QWEN", "OPENPOSE")], cw, ch)


# ---------------------------------------------------------------- 人工評分（目視 processed/*.png；1–5）
# key: (candidate, action, seed) → (consistency, action_score, notes)
SCORES: dict[tuple[str, str, int], tuple[int, int, str]] = {}
_A = dict(w="waving one hand", h="holding something with both hands", j="jumping happily", r="running", s="sitting",
          b="reading a book")
for _c, _rows in {
    "BASELINE": [
        ("w", 101, 3, 1, "scarf on neck; ears more upright/one droops; chubbier; arms just out, no wave"),
        ("w", 202, 2, 1, "extra red cloth wrapped on head (scarf-on-head bug); ears upright; standing, no wave"),
        ("h", 101, 3, 1, "nearly identical to waving s101; nothing held"),
        ("h", 202, 2, 1, "red head wrap; ears upright; nothing held"),
        ("j", 101, 3, 2, "same template pose; feet slightly lifted, weak jump"),
        ("j", 202, 2, 2, "red head wrap; legs tucked, reads as hop"),
        ("r", 101, 3, 2, "one leg forward, weak run"),
        ("r", 202, 2, 2, "red head wrap; legs in motion"),
        ("s", 101, 3, 4, "sits on haunches; red puddle under body; ears upright"),
        ("s", 202, 2, 3, "red headband; upright ears; sitting-ish"),
        ("b", 101, 3, 2, "book-like shape at hip only, not reading"),
        ("b", 202, 2, 1, "red head wrap; no book"),
    ],
    "QWEN": [
        ("w", 101, 4, 5, "same design/ears/face; clear wave; raised arm gets red sleeve; buck teeth added; ground shadow kept"),
        ("w", 202, 4, 5, "near duplicate of s101 (low seed diversity); red sleeve"),
        ("h", 101, 4, 5, "holds a box with both paws; red sleeves; shadow"),
        ("h", 202, 4, 5, "holds a fruit with both paws; red sleeves"),
        ("j", 101, 3, 5, "great jump; ears turn upright with pink inside, closed-eye face; red sleeves; detached shadow + motion lines"),
        ("j", 202, 3, 5, "same as s101; detached shadow -> 2nd mask component"),
        ("r", 101, 4, 5, "clear side-ish run; red sleeves; smeared ground shadow -> mask not clean"),
        ("r", 202, 4, 5, "clear run; detached shadow -> mask not clean"),
        ("s", 101, 5, 5, "sits on the ground, scarf as cape, ears/face match"),
        ("s", 202, 5, 5, "sits, very faithful"),
        ("b", 101, 4, 5, "standing, reading an open book; red sleeves"),
        ("b", 202, 4, 5, "reading an open book; red sleeves"),
    ],
    "OPENPOSE": [
        ("w", 101, 4, 3, "ears sideways like ref, scarf on neck; paw raised but touches head (salute-like); slimmer body"),
        ("w", 202, 3, 4, "arm up waving; stray ear lines above head; blush cheeks"),
        ("h", 101, 4, 2, "paws clasped at chest, nothing held"),
        ("h", 202, 2, 2, "duplicated ears (4 ears); nothing held"),
        ("j", 101, 4, 3, "one knee up, mid-air-ish"),
        ("j", 202, 4, 3, "arms out, feet off ground, floating"),
        ("r", 101, 4, 3, "leg lifted + arm swing, awkward"),
        ("r", 202, 2, 3, "extra ear; stride"),
        ("s", 101, 2, 1, "standing, third upright ear; sitting skeleton ignored"),
        ("s", 202, 1, 1, "4 ears + red headband; standing"),
        ("b", 101, 2, 1, "4 ears; no book; standing"),
        ("b", 202, 1, 1, "4 ears + headband; no book"),
    ],
    "OPENPOSE_cn0.6": [
        ("w", 101, 4, 4, "arm up by head"), ("w", 202, 3, 4, "arm up; blush"),
        ("h", 101, 4, 2, "paws clasped, nothing held"), ("h", 202, 2, 2, "4 ears"),
        ("j", 101, 4, 3, "knee up"), ("j", 202, 4, 3, "arms out, floating"),
        ("r", 101, 4, 2, "weak stride"), ("r", 202, 2, 2, "extra ears"),
        ("s", 101, 2, 1, "standing, extra upright ear"), ("s", 202, 1, 1, "4 ears + headband, standing"),
        ("b", 101, 2, 1, "4 ears, no book"), ("b", 202, 1, 1, "4 ears + headband, no book"),
    ],
    "OPENPOSE_cn0.8": [
        ("w", 101, 4, 3, "paw at head"), ("w", 202, 2, 4, "arm up; extra ears"),
        ("h", 101, 4, 2, "paws clasped, nothing held"), ("h", 202, 2, 2, "4 ears"),
        ("j", 101, 4, 3, "knee up"), ("j", 202, 4, 3, "arms out, floating"),
        ("r", 101, 4, 3, "stride + arm swing"), ("r", 202, 2, 2, "extra ears"),
        ("s", 101, 2, 1, "standing, extra upright ear"), ("s", 202, 1, 1, "4 ears + headband, standing"),
        ("b", 101, 2, 1, "4 ears, no book"), ("b", 202, 1, 1, "4 ears + headband, no book"),
    ],
}.items():
    for _k, _s, _cs, _as, _n in _rows:
        SCORES[(_c, _A[_k], _s)] = (_cs, _as, _n)


def write_csv():
    metrics = json.loads((OUT / "metrics.json").read_text())
    with open(HERE / "results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "action", "seed", "time_s", "mask_ok", "gaps", "small_regions", "consistency",
                    "action_score", "notes"])
        for r in metrics.values():
            sc = SCORES.get((r["candidate"], r["action"], r["seed"]), ("", "", ""))
            if "error" in r:
                w.writerow([r["candidate"], r["action"], r["seed"], "", "", "", "", "", "",
                            "FAILED: " + r["error"][:200]])
                continue
            note = sc[2] + f" [mask area {r['mask_area_ratio']}, main {r['mask_main_share']}]"
            w.writerow([r["candidate"], r["action"], r["seed"], r["time_s"], r["mask_ok"], r["gaps"],
                        r["small_regions"], sc[0], sc[1], note.strip()])
    print(HERE / "results.csv")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        gen(sys.argv[2:])
    elif cmd == "sheets":
        sheets()
    elif cmd == "csv":
        write_csv()
    elif cmd == "skeletons":
        (OUT / "skeletons").mkdir(parents=True, exist_ok=True)
        for a in ACTIONS:
            draw_skeleton(a).save(OUT / "skeletons" / f"{slug(a)}.png")
