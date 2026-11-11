"""Phase 12 spike (throwaway): pose variation, expressions, facing, and the extra-limb rate.

E1 limbs: 16 actions × {T3 (production), T3A (+ positive anatomy sentence)} on the bunny and the girl (8 actions).
E2 expressions: 8 expressions combined with "standing" (bunny, T3A).
E3 facing: left / front / right × 2 actions (bunny, T3A).
E4 regenerate variation: baseline seeds 101/202 vs 4 candidate variation phrases (bunny, waving + jumping, T3A).
CFG is 1.0 (Lightning), so only positive wording can steer. Scoring is visual (sheets).

Usage (repo root): PY="env -u PYTHONPATH PYTHONPATH=backend:. /ai-data/venvs/ubuntu24.04/coloring-books/bin/python"
  $PY experiments/results/phase12_pose_spike_20261111/run.py run|free|report
"""
import importlib.util, statistics as st, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("p9", HERE.parent / "phase9_all_qwen_20261110_1700" / "run.py")
p9 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p9)
p9.OUT, p9.SHEETS = HERE / "processed", HERE / "sheets"
p9.MPATH = p9.OUT / "metrics.json"

from app.services.prompts import QWEN_POSE_TEMPLATE as T3  # noqa: E402

ANAT = ("Correct anatomy: exactly two arms, two hands and two legs, or only the limbs this character's design has; "
        "no extra arms, hands, legs or tails. ")
T3A = T3.replace("Keep the same character design", ANAT + "Keep the same character design", 1)
assert T3A != T3
ACTIONS = {  # existing presets + candidates for new buttons (EN phrases as the buttons insert them)
    "stand": "standing", "walk": "walking", "run": "running", "wave": "waving one hand",
    "point": "pointing forward", "hold": "holding something with both hands", "jump": "jumping happily",
    "read": "reading a book", "clap": "clapping hands", "hug_ball": "hugging a big ball",
    "kick": "kicking a ball", "pick": "bending down to pick something up", "stretch": "stretching both arms up high",
    "dance": "dancing", "eat": "eating an apple", "sleep": "lying down and sleeping",
}
GIRL_ACTIONS = ["run", "wave", "hold", "jump", "clap", "kick", "dance", "eat"]
EXPR = {"happy": "a happy smiling", "laugh": "a big laughing", "sad": "a sad", "angry": "an angry",
        "surprised": "a surprised", "shy": "a shy blushing", "sleepy": "a sleepy", "scared": "a scared"}
FACING = {"left": "facing toward the left side of the picture", "front": "facing the viewer",
          "right": "facing toward the right side of the picture"}
VARS = {"V1": "seen in a three-quarter view turned slightly to the left",
        "V2": "seen in a three-quarter view turned slightly to the right",
        "V3": "with a bigger, more energetic motion",
        "V4": "with a calmer, smaller motion"}


def jobs():
    J = []  # (stem, char, prompt, seed)
    for k, a in ACTIONS.items():
        for t, tpl in (("T3", T3), ("T3A", T3A)):
            J.append((f"limb__bunny__{k}__{t}", "bunny", tpl.format(action=a), 101))
    for k in GIRL_ACTIONS:
        for t, tpl in (("T3", T3), ("T3A", T3A)):
            J.append((f"limb__girl__{k}__{t}", "girl", tpl.format(action=ACTIONS[k]), 101))
    for k, e in EXPR.items():
        J.append((f"expr__bunny__{k}", "bunny", T3A.format(action=f"standing, with {e} expression"), 101))
    for f, ph in FACING.items():
        for a in ("walk", "wave"):
            J.append((f"face__bunny__{a}__{f}", "bunny", T3A.format(action=f"{ACTIONS[a]}, {ph}"), 101))
    for a in ("wave", "jump"):
        J.append((f"var__bunny__{a}__base_s202", "bunny", T3A.format(action=ACTIONS[a]), 202))
        for v, ph in VARS.items():
            J.append((f"var__bunny__{a}__{v}", "bunny", T3A.format(action=f"{ACTIONS[a]}, {ph}"), 101))
    return J


def run():
    m = p9.load()
    entry = p9.REG.get("qwen_edit_pose_cutout")
    p9.gpu_idle_wait()
    up = {}
    for stem, ch, p, s in jobs():
        if stem in m:
            continue
        if ch not in up:
            up[ch] = p9.upload(p9.OUT / f"ref_{ch}.png")
        vals = {"prompt": p, "seed": s, "width": 832, "height": 1216, "character_image": up[ch]}
        outs, dt = p9.run(entry.build_prompt(vals).prompt)
        img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
        img.save(p9.OUT / f"{stem}.png")
        mask.save(p9.OUT / f"{stem}_mask.png")
        m[stem] = {"time_s": dt, **p9.pose_metrics(img, mask, stem), "prompt": p, "seed": s}
        p9.save(m)
        print(stem, {k: v for k, v in m[stem].items() if k != "prompt"}, flush=True)


def report():
    O = p9.OUT
    c = lambda s: O / f"{s}_cut.png"  # noqa: E731
    rows = [(k, [c(f"limb__bunny__{k}__T3"), c(f"limb__bunny__{k}__T3A")]) for k in ACTIONS]
    p9.grid(rows[:8], ["T3", "T3A"], "sheet_limbs_bunny_1.jpg", cw=260, ch=380, lw=80)
    p9.grid(rows[8:], ["T3", "T3A"], "sheet_limbs_bunny_2.jpg", cw=260, ch=380, lw=80)
    rows = [(k, [c(f"limb__girl__{k}__T3"), c(f"limb__girl__{k}__T3A")]) for k in GIRL_ACTIONS]
    p9.grid(rows, ["T3", "T3A"], "sheet_limbs_girl.jpg", cw=260, ch=380, lw=80)
    p9.grid([(k, [c(f"expr__bunny__{k}")]) for k in EXPR], ["expression"], "sheet_expressions.jpg",
            cw=260, ch=380, lw=90)
    p9.grid([(a, [c(f"face__bunny__{a}__{f}") for f in FACING]) for a in ("walk", "wave")], list(FACING),
            "sheet_facing.jpg", cw=260, ch=380, lw=60)
    p9.grid([(a, [c(f"limb__bunny__{a}__T3A"), c(f"var__bunny__{a}__base_s202")] +
              [c(f"var__bunny__{a}__{v}") for v in VARS]) for a in ("wave", "jump")],
            ["s101", "s202", *VARS], "sheet_variations.jpg", cw=220, ch=320, lw=60)
    m = p9.load()
    for g in ("limb", "expr", "face", "var"):
        r = [v for k, v in m.items() if k.startswith(g + "__")]
        if r:
            print(g, len(r), "gaps", round(st.mean(x["gaps"] for x in r), 1), "small",
                  round(st.mean(x["small_regions"] for x in r), 1), "time", round(st.mean(x["time_s"] for x in r), 1))


# ---- round 2: T3's "plus the vehicle or object the action mentions" invents objects when the action names none
# (girl jump → a taxi under her; "holding something" → a toy bus). T4 only allows objects the action names.
T4 = T3.replace("Draw only the character, plus the vehicle or object the action mentions, completely visible;",
                "Draw only the character. If the action names a vehicle or an object, also draw that one thing, "
                "completely visible; if it names none, add nothing else;", 1)
assert T4 != T3
T4A = T4.replace("Keep the same character design", ANAT + "Keep the same character design", 1)
R2 = {  # (char, action) → seeds
    ("girl", "jumping happily"): (101, 202, 303), ("girl", "holding something with both hands"): (101, 202),
    ("bunny", "holding something with both hands"): (101, 202), ("bunny", "dancing"): (101,),
    ("bunny", "開著紅色消防車，車上有長梯子，從車窗探出頭揮手"): (101,),
    ("bunny", "坐在黃色公車的駕駛座上揮手，畫出整台公車的外觀"): (101,),
    ("bunny", "騎著腳踏車"): (101,), ("girl", "雙手捧著一顆紅蘋果"): (101,),
}


def run2():
    m = p9.load()
    entry = p9.REG.get("qwen_edit_pose_cutout")
    p9.gpu_idle_wait()
    up = {}
    for i, ((ch, a), seeds) in enumerate(R2.items()):
        for s in seeds:
            for t, tpl in (("T4", T4), ("T4A", T4A)):
                stem = f"r2__{ch}__a{i}__{t}__s{s}"
                if stem in m:
                    continue
                if ch not in up:
                    up[ch] = p9.upload(p9.OUT / f"ref_{ch}.png")
                vals = {"prompt": tpl.format(action=a), "seed": s, "width": 832, "height": 1216,
                        "character_image": up[ch]}
                outs, dt = p9.run(entry.build_prompt(vals).prompt)
                img, mask = outs["OUTPUT"].convert("RGB"), outs["MASK"].convert("L")
                img.save(p9.OUT / f"{stem}.png")
                mask.save(p9.OUT / f"{stem}_mask.png")
                m[stem] = {"time_s": dt, **p9.pose_metrics(img, mask, stem), "prompt": vals["prompt"], "seed": s,
                           "action": a}
                p9.save(m)
                print(stem, {k: v for k, v in m[stem].items() if k not in ("prompt",)}, flush=True)


def report2():
    O = p9.OUT
    rows = []
    for i, ((ch, a), seeds) in enumerate(R2.items()):
        for s in seeds:
            rows.append((f"{ch[:5]} a{i} s{s}", [O / f"r2__{ch}__a{i}__{t}__s{s}_cut.png" for t in ("T4", "T4A")]))
    p9.grid(rows, ["T4", "T4A"], "sheet_r2_objects.jpg", cw=220, ch=320, lw=110)


if __name__ == "__main__":
    {"run": run, "free": p9.free, "report": report, "run2": run2, "report2": report2}[sys.argv[1]]()
