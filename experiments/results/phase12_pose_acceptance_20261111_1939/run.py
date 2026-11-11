"""Phase 12 real-GPU acceptance (spec §4): production prompt functions from this branch, direct to ComfyUI 8188.

- 揮手 regenerated 3× on one page: each picks a variation excluding the previous one (like _pose_job) → 3 visibly different poses.
- 揮手＋大笑, 跳舞＋害羞 combinations.
- 朝左 + 2 regenerations → only variations 3/4 (motion size).
- girl 跳起來 → no extra object; vehicle-pack actions 消防車/公車/腳踏車 still drawn.
Usage (worktree root): env -u PYTHONPATH PYTHONPATH=backend:. <venv python> experiments/results/phase12_pose_acceptance*/run.py run|free|report
"""
import importlib.util, json, random, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("p9", HERE.parent / "phase9_all_qwen_20261110_1700" / "run.py")
p9 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p9)
p9.OUT, p9.SHEETS = HERE / "processed", HERE / "sheets"
p9.MPATH = p9.OUT / "metrics.json"

from app.services.prompts import has_facing, pick_pose_variation, qwen_pose_prompt  # noqa: E402

WAVE, LAUGH = "waving one hand", "with a big laughing expression"
SEQS = {  # name -> (char, action, regenerations)
    "wave": ("bunny", WAVE, 3),
    "wave_laugh": ("bunny", f"{WAVE}, {LAUGH}", 1),
    "dance_shy": ("bunny", "dancing, with a shy blushing expression", 1),
    "walk_left": ("bunny", "walking, facing toward the left side of the picture", 3),
    "girl_jump": ("girl", "跳起來", 2),
    "fire": ("bunny", "開著紅色消防車，車上有長梯子，從車窗探出頭揮手", 1),
    "bus": ("bunny", "坐在黃色公車的駕駛座上揮手，畫出整台公車的外觀", 1),
    "bike": ("bunny", "騎著腳踏車", 1),
}


def run():
    m = p9.load()
    entry = p9.REG.get("qwen_edit_pose_cutout")
    p9.gpu_idle_wait()
    up = {}
    rng = random.Random(20261111)
    for name, (ch, action, n) in SEQS.items():
        prev = None
        for i in range(n):
            stem = f"{name}__{i + 1}"
            if stem in m:
                prev = m[stem]["variation"]
                continue
            if ch not in up:
                up[ch] = p9.upload(p9.OUT / f"ref_{ch}.png")
            v = pick_pose_variation(rng, exclude=prev, facing=has_facing(action))
            seed = rng.randint(0, 2**32 - 1)
            vals = {"prompt": qwen_pose_prompt(action, v), "seed": seed, "width": 832, "height": 1216,
                    "character_image": up[ch]}
            outs, dt = p9.run(entry.build_prompt(vals).prompt)
            img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
            img.save(p9.OUT / f"{stem}.png")
            mask.save(p9.OUT / f"{stem}_mask.png")
            m[stem] = {"time_s": dt, "variation": v, "seed": seed, "action": action,
                       **p9.pose_metrics(img, mask, stem), "prompt": vals["prompt"]}
            p9.save(m)
            prev = v
            print(stem, {k: x for k, x in m[stem].items() if k != "prompt"}, flush=True)


def report():
    O = p9.OUT
    rows = [(name, [O / f"{name}__{i + 1}_cut.png" for i in range(n)]) for name, (_, _, n) in SEQS.items()]
    p9.grid(rows, ["1", "2", "3"], "sheet_acceptance.jpg", cw=230, ch=336, lw=90)
    m = p9.load()
    for k, v in m.items():
        print(k, v.get("variation"), v.get("gaps"), v.get("small_regions"), v.get("time_s"))


if __name__ == "__main__":
    {"run": run, "free": p9.free, "report": report}[sys.argv[1]]()
