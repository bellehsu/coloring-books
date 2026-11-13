"""Phase 14 #8 — outline wording calibration (GPU, throwaway). Spec §4.8.

2 character descriptions × 3 wordings of OUTLINE_PHRASE (thick = today's baseline, thin = candidate, medium).
Each wording replaces OUTLINE_PHRASE in the product's character / pose / background templates.
Per character: 2 poses (qwen_edit_pose_cutout from that wording's character) + 1 background. Fixed seeds.
Metrics: outline half-thickness (skeleton of luma<70, distance transform p75/p90, at 832 px width) and the product line
art with outline_fill=True (QA gap_count).

Usage: env -u PYTHONPATH PYTHONPATH=backend:. python <this> run|sheet|free
"""
import csv, io, json, sys, time, uuid
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.morphology import skeletonize

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.services import prompts  # noqa: E402
from app.services.lineart_render import LineDetail, ink_to_png, render_lineart  # noqa: E402
from app.services.qa import evaluate  # noqa: E402
from app.services.workflow_registry import WorkflowRegistry  # noqa: E402

C = "http://127.0.0.1:8188"
OUT, SHEETS = HERE / "processed", HERE / "sheets"
CONTENT_PX, LINE_W = (2244, 3272), 11
WORDINGS = {"thick": "thick clean black outlines", "thin": "clean thin black outlines",
            "medium": "clean medium-weight black outlines"}
CHARS = {
    "fox": ("一隻圓圓胖胖的小狐狸，橘色的毛、白色的肚子，脖子上圍著藍色的小圍巾", ["揮手打招呼", "開心地跳起來"],
            "森林裡的小路，有大樹和蘑菇"),
    "bear": ("一隻棕色的小熊，圓圓的耳朵，穿著紅色的吊帶褲", ["坐在地上看書", "張開雙手奔跑"],
             "溫馨的臥室，有一張床、一個書架和一扇窗戶"),
}
CHAR_SEED, POSE_SEED, BG_SEED = 1001, 101, 101
BG_SIZE = (864, 1216)
cli = httpx.Client(timeout=900)
REG = WorkflowRegistry(REPO / "comfy_workflows").load()


def tpl(name: str, wording: str) -> str:
    t = getattr(prompts, name)
    assert t.count(prompts.OUTLINE_PHRASE) == 1, name
    return t.replace(prompts.OUTLINE_PHRASE, wording)


def run(wf: dict):
    t = time.time()
    r = cli.post(f"{C}/prompt", json={"prompt": wf})
    if r.status_code != 200:
        raise RuntimeError(r.text[:800])
    pid = r.json()["prompt_id"]
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
        time.sleep(0.3)


def upload(p: Path) -> str:
    r = cli.post(f"{C}/upload/image", files={"image": (f"p14o_{uuid.uuid4().hex[:8]}.png", p.read_bytes(), "image/png")},
                 data={"overwrite": "true"})
    r.raise_for_status()
    return r.json()["name"]


def thickness(rgb: np.ndarray) -> tuple[float, float]:
    w = rgb.shape[1]
    luma = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    d = (luma < 70).astype(np.uint8)
    dt = cv2.distanceTransform(d, cv2.DIST_L2, 5) * (832 / w)
    v = dt[skeletonize(d > 0)]
    v = v[(v >= 1) & (v <= 20)]
    if v.size == 0:
        return 0.0, 0.0
    return round(float(np.percentile(v, 75)), 2), round(float(np.percentile(v, 90)), 2)


def measure(rgb: np.ndarray, stem: str) -> dict:
    ink = render_lineart(rgb, CONTENT_PX, LineDetail.STANDARD, LINE_W, outline_fill=True)
    (OUT / f"{stem}_lineart.png").write_bytes(ink_to_png(ink))
    rep = evaluate(ink, LINE_W)
    p75, p90 = thickness(rgb)
    return {"half_p75": p75, "half_p90": p90, "gaps": rep.gap_count}


def on_white(img: Image.Image, mask: Image.Image) -> np.ndarray:
    a = (np.asarray(mask.convert("L"), np.float32) / 255.0)[..., None]
    return (np.asarray(img.convert("RGB"), np.float32) * a + 255.0 * (1 - a)).clip(0, 255).astype(np.uint8)


def main_run():
    mpath = OUT / "metrics.json"
    m = json.loads(mpath.read_text()) if mpath.exists() else {}
    save = lambda: mpath.write_text(json.dumps(m, indent=1, ensure_ascii=False))  # noqa: E731
    ch_e, pose_e, bg_e = REG.get("qwen_character"), REG.get("qwen_edit_pose_cutout"), REG.get("qwen_background")
    for wk, wording in WORDINGS.items():
        for ck, (desc, actions, scene) in CHARS.items():
            stem = f"{wk}__{ck}__char"
            if stem not in m:
                p = tpl("QWEN_CHARACTER_TEMPLATE", wording).format(description=desc, variation="")
                outs, dt = run(ch_e.build_prompt({"prompt": p, "seed": CHAR_SEED, "width": 832, "height": 1216}).prompt)
                img = outs["OUTPUT"].convert("RGB"); img.save(OUT / f"{stem}.png")
                m[stem] = {"wording": wk, "char": ck, "kind": "char", "time_s": dt, **measure(np.asarray(img), stem)}
                save(); print(stem, m[stem], flush=True)
            ref = upload(OUT / f"{stem}.png")
            for i, act in enumerate(actions):
                ps = f"{wk}__{ck}__pose{i + 1}"
                if ps in m:
                    continue
                p = tpl("QWEN_POSE_TEMPLATE", wording).format(action=act)
                outs, dt = run(pose_e.build_prompt({"prompt": p, "seed": POSE_SEED, "width": 832, "height": 1216,
                                                    "character_image": ref}).prompt)
                rgb = on_white(outs["OUTPUT"], outs["MASK"]); Image.fromarray(rgb).save(OUT / f"{ps}.png")
                m[ps] = {"wording": wk, "char": ck, "kind": "pose", "action": act, "time_s": dt, **measure(rgb, ps)}
                save(); print(ps, m[ps], flush=True)
            bs = f"{wk}__{ck}__bg"
            if bs not in m:
                p = tpl("QWEN_BACKGROUND_TEMPLATE", wording).format(scene=scene, variation="")
                outs, dt = run(bg_e.build_prompt({"prompt": p, "seed": BG_SEED, "width": BG_SIZE[0],
                                                  "height": BG_SIZE[1]}).prompt)
                img = outs["OUTPUT"].convert("RGB"); img.save(OUT / f"{bs}.png")
                m[bs] = {"wording": wk, "char": ck, "kind": "bg", "time_s": dt, **measure(np.asarray(img), bs)}
                save(); print(bs, m[bs], flush=True)
    with open(HERE / "results.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["stem", "wording", "char", "kind", "half_p75", "half_p90", "gaps", "time_s"])
        for k, v in m.items():
            w.writerow([k, v["wording"], v["char"], v["kind"], v["half_p75"], v["half_p90"], v["gaps"], v["time_s"]])


def sheet():
    font = ImageFont.truetype(str(REPO / "assets/fonts/NotoSansTC-VF.ttf"), 30)
    cols = ["char", "pose1", "pose2", "bg"]
    tw, th = 300, 438
    for ck in CHARS:
        S = Image.new("RGB", (160 + len(cols) * 2 * (tw + 6), 50 + len(WORDINGS) * (th + 10)), "white")
        d = ImageDraw.Draw(S)
        for j, c in enumerate(cols):
            d.text((160 + j * 2 * (tw + 6) + 10, 8), f"{c} 彩色 / 線稿", font=font, fill="black")
        for i, wk in enumerate(WORDINGS):
            y = 50 + i * (th + 10)
            d.text((10, y + th // 2), wk, font=font, fill="black")
            for j, c in enumerate(cols):
                stem = f"{wk}__{ck}__{c}"
                for k, suffix in enumerate(["", "_lineart"]):
                    im = Image.open(OUT / f"{stem}{suffix}.png").convert("RGB")
                    im.thumbnail((tw, th))
                    S.paste(im, (160 + j * 2 * (tw + 6) + k * (tw + 6), y))
        S.save(SHEETS / f"sheet_{ck}.jpg", quality=88)
    # close-up of characters: color only, crop centre
    S = Image.new("RGB", (len(WORDINGS) * 2 * 410, 2 * 560 + 60), "white"); d = ImageDraw.Draw(S)
    for i, wk in enumerate(WORDINGS):
        d.text((i * 820 + 10, 8), wk, font=font, fill="black")
        for r, ck in enumerate(CHARS):
            for k, suffix in enumerate(["", "_lineart"]):
                im = Image.open(OUT / f"{wk}__{ck}__pose1{suffix}.png").convert("RGB")
                W, H = im.size
                im = im.crop((int(.2 * W), int(.15 * H), int(.8 * W), int(.6 * H))).resize((400, 550))
                S.paste(im, (i * 820 + k * 410, 50 + r * 560))
    S.save(SHEETS / "closeup_pose1.jpg", quality=90)


if __name__ == "__main__":
    {"run": main_run, "sheet": sheet,
     "free": lambda: print(cli.post(f"{C}/free", json={"unload_models": True, "free_memory": True}).status_code)}[sys.argv[1]]()
