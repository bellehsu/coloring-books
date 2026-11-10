"""Phase 6 Task 3：精準模式（qwen_edit_pose_cutout）提示詞校正。拋棄式實驗腳本。

用正式 workflow（comfy_workflows/qwen_edit_pose_cutout，經 WorkflowRegistry.build_prompt）跑：
同一隻參考兔 × 6 個動作 × 2 seed × 提示詞版本；遮罩先做「只留最大連通塊」再量線稿指標。
用法（repo 根目錄）：
  env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python \
      experiments/results/phase6_qwen_pose_calibration_20261110/run.py gen V1 V2 V3
  ... run.py sheets
"""
import io, json, subprocess, sys, time, uuid
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path("/ai-data/repos/coloring-books")
sys.path.insert(0, str(REPO / "backend"))
from app.services.lineart_render import LineDetail, ink_to_png, render_base  # noqa: E402
from app.services.qa import evaluate  # noqa: E402
from app.services.workflow_registry import WorkflowRegistry  # noqa: E402

C = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
OUT = HERE / "processed"
REF = Path("/ai-data/temp/ubuntu24.04/claude-1000/-ai-data-repos-coloring-books/"
           "0571fc5b-d4ca-4525-ba7b-da0150cd1ebc/scratchpad/p5acc/storage/books/1/generations/1.png")
ACTIONS = ["waving one hand", "holding something with both hands", "jumping happily", "running", "sitting",
           "reading a book"]
SEEDS = [101, 202]
CONTENT_PX = (2244, 3272)
LINE_W = 11

BASE = ("Make this exact character {action}. Keep the same character design, proportions, colors, scarf and "
        "ears unchanged. Full body, centered, plain pure white background, children's picture book "
        "illustration, flat colors, thick clean black outlines.")
FIX = ("Make this exact character {action}. Keep the same character design, proportions, colors and ears "
       "unchanged. The red scarf is wrapped only around the neck; the arms and paws are white fur, no sleeves. "
       "Full body, centered, plain pure white background with no ground shadow and nothing under the feet, "
       "children's picture book illustration, flat colors, thick clean black outlines.")
VARIATIONS = ["facing slightly to the left", "facing slightly to the right"]  # V3／V5：每個 seed 換一個
# 第二輪：V2 的「nothing under the feet」讓 sitting 變站姿 → 拿掉；圍巾改成「照參考圖圍在脖子上」保留垂下的長度
FIX2 = ("Make this exact character {action}. Keep the same character design, proportions, colors, scarf and ears "
        "unchanged. The red scarf stays around the neck as in the reference image, never on the head and never "
        "as sleeves; the arms and paws are white fur. Full body, centered, plain pure white background, no shadow, "
        "children's picture book illustration, flat colors, thick clean black outlines.")


# 第三輪：回到 V1 的措辭（最貼近參考圖的圍巾垂墜與耳朵），只加「no shadow」；sitting 另測 "sitting on the ground"
BASE_NS = BASE.replace("plain pure white background,", "plain pure white background, no shadow, no cast shadow,")


def prompt_for(ver: str, action: str, seed: int) -> str:
    if ver in ("V6", "V7") and action == "sitting":
        action = "sitting on the ground"
    if ver == "V6":
        return BASE_NS.format(action=action)
    if ver == "V7":
        return BASE_NS.format(action=f"{action}, {VARIATIONS[SEEDS.index(seed) % len(VARIATIONS)]}")
    if ver == "V1":
        return BASE.format(action=action)
    if ver == "V2":
        return FIX.format(action=action)
    var = VARIATIONS[SEEDS.index(seed) % len(VARIATIONS)]
    if ver == "V3":
        return FIX.format(action=f"{action}, {var}")
    if ver == "V4":
        return FIX2.format(action=action)
    return FIX2.format(action=f"{action}, {var}")  # V5


cli = httpx.Client(timeout=600)
REG = WorkflowRegistry(REPO / "comfy_workflows").load()
ENTRY = REG.get("qwen_edit_pose_cutout")


def upload(p: Path) -> str:
    r = cli.post(f"{C}/upload/image", files={"image": (f"p6cal_{uuid.uuid4().hex[:8]}.png", p.read_bytes(),
                                                       "image/png")}, data={"overwrite": "true"})
    r.raise_for_status()
    return r.json()["name"]


def run(wf: dict) -> dict[str, Image.Image]:
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
            return out
        time.sleep(0.5)


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


def largest(mask: np.ndarray) -> tuple[np.ndarray, int]:
    b = (mask > 128).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    if n <= 1:
        return np.zeros_like(mask), 0
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    big = int((stats[1:, cv2.CC_STAT_AREA] >= 0.005 * b.sum()).sum())
    return np.where(lab == k, 255, 0).astype(np.uint8), big


def analyse(img: Image.Image, mask: Image.Image, stem: str) -> dict:
    m_raw = np.asarray(mask.convert("L"))
    m, big_before = largest(m_raw)
    Image.fromarray(m).save(OUT / f"{stem}_mask_lc.png")
    a = m.astype(np.float32)[..., None] / 255.0
    rgb = np.asarray(img.convert("RGB"), np.float32)
    cut = (rgb * a + 255.0 * (1 - a)).clip(0, 255).astype(np.uint8)
    Image.fromarray(cut).save(OUT / f"{stem}_cut.png")
    ink, _ = render_base(cut, CONTENT_PX, LineDetail.STANDARD, LINE_W)
    (OUT / f"{stem}_lineart.png").write_bytes(ink_to_png(ink))
    rep = evaluate(ink, LINE_W)
    ratio = float((m > 0).mean())
    return {"pieces_before": big_before, "mask_area_ratio": round(ratio, 4), "mask_ok": bool(0.05 <= ratio <= 0.8),
            "gaps": rep.gap_count, "small_regions": rep.metrics["tiny_region_count"]}


def gen(vers: list[str]):
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "metrics.json"
    metrics = json.loads(mpath.read_text()) if mpath.exists() else {}
    ref = upload(REF)
    for ver in vers:
        gpu_idle_wait()
        for a in ACTIONS:
            for s in SEEDS:
                stem = f"{ver}_{a.replace(' ', '_')}_s{s}"
                if stem in metrics and "error" not in metrics[stem]:
                    continue
                p = prompt_for(ver, a, s)
                built = ENTRY.build_prompt({"prompt": p, "seed": s, "width": 832, "height": 1216,
                                            "character_image": ref})
                t = time.time()
                try:
                    outs = run(built.prompt)
                except Exception as e:  # noqa: BLE001
                    metrics[stem] = {"ver": ver, "action": a, "seed": s, "error": str(e)[:800]}
                    print(stem, "ERROR", str(e)[:300], flush=True)
                    mpath.write_text(json.dumps(metrics, indent=1))
                    continue
                dt = round(time.time() - t, 1)
                img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
                img.save(OUT / f"{stem}.png")
                mask.save(OUT / f"{stem}_mask.png")
                metrics[stem] = {"ver": ver, "action": a, "seed": s, "prompt": p, "time_s": dt,
                                 **analyse(img, mask, stem)}
                mpath.write_text(json.dumps(metrics, indent=1))
                print(stem, {k: v for k, v in metrics[stem].items() if k != "prompt"}, flush=True)


FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)


def sheet(vers: list[str], suffix: str, name: str):
    cw, ch, lab = 230, 336, 24
    cols = [(v, s) for v in vers for s in SEEDS]
    img = Image.new("RGB", ((len(cols) + 1) * cw, (len(ACTIONS) + 1) * (ch + lab)), "white")
    d = ImageDraw.Draw(img)
    r = Image.open(REF).convert("RGB"); r.thumbnail((cw, ch)); img.paste(r, (0, lab))
    d.text((4, 3), "REFERENCE", fill="black", font=FONT)
    for j, (v, s) in enumerate(cols):
        d.text(((j + 1) * cw + 4, 3), f"{v} s{s}", fill="black", font=FONT)
    for i, a in enumerate(ACTIONS):
        y = (i + 1) * (ch + lab)
        d.text((4, y + ch // 2), a.replace(" with", "\nwith").replace("something", "sth"), fill="black", font=FONT)
        for j, (v, s) in enumerate(cols):
            p = OUT / f"{v}_{a.replace(' ', '_')}_s{s}{suffix}.png"
            if p.exists():
                t = Image.open(p).convert("RGB"); t.thumbnail((cw - 6, ch)); img.paste(t, ((j + 1) * cw, y + lab))
    img.save(OUT / name, quality=85)
    print(OUT / name, img.size)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        gen(sys.argv[2:])
    elif cmd == "sheets":
        vs = sys.argv[2:] or ["V1", "V2", "V3"]
        tag = "_".join(vs)
        sheet(vs, "_cut", f"sheet_cut_{tag}.jpg")
        sheet(vs, "_lineart", f"sheet_lineart_{tag}.jpg")
