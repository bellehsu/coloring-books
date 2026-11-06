import copy
import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from experiments import run_matrix as rm
from tests.conftest import REPO, TINY_MANIFEST, write_workflow
from tests.fake_comfy import FakeComfy, png_bytes


def sketch_png() -> bytes:
    img = np.full((240, 180), 255, np.uint8)
    cv2.circle(img, (90, 120), 60, 0, 4)
    cv2.rectangle(img, (40, 30), (140, 60), 0, 3)
    img[200, 20] = 0  # 雜點
    return png_bytes(img)


@pytest.fixture
def env(tmp_path, settings_file, object_info_subset, monkeypatch):
    """兩條路線都用 tiny workflow（第二份名為 tiny_b），ComfyUI 以 FakeComfy 取代。"""
    wf_dir = Path(yaml.safe_load(settings_file.read_text())["paths"]["workflows_dir"])
    m = copy.deepcopy(TINY_MANIFEST)
    write_workflow(wf_dir, "tiny_b", None, m)
    ref = tmp_path / "ref.png"
    ref.write_bytes(png_bytes())

    from tests.test_preflight import tiny_object_info
    fake = FakeComfy(object_info=tiny_object_info(object_info_subset), image=sketch_png())
    monkeypatch.setattr(rm, "ComfyClient", lambda url, **kw: fake.client(**kw))

    cfg = {
        "name": "t",
        "output_dir": str(tmp_path / "results"),
        "character": {"description": "a bunny", "reference_image": str(ref)},
        "seeds": [1, 2],
        "postprocess": {"target_width": 180, "target_height": 240, "line_width_px": 3, "min_fragment_area": 4,
                        "min_region_area": 50},
        "routes": {
            "A": {"workflow": "tiny", "params": {"ref_image": "{character_image}",
                                                 "prompt": "{character}, {scene}, {lineart} {keep}"}},
            "B": {"workflow": "tiny_b", "params": {"ref_image": "{character_image}", "prompt": "B: {scene}"}},
        },
        "lineart_methods": {
            "plain": {"prompt": "line art", "route_params": {"A": {"steps": 12}}},
            "guided": {"prompt": "guided", "routes": ["A"], "postprocess": {"close_kernel": 3}},
        },
        "scenes": {"park": {"prompt": "in a park", "route_params": {"B": {"cfg": 2.0}}}},
    }
    cfg_path = tmp_path / "matrix.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return {"cfg_path": cfg_path, "settings": settings_file, "fake": fake, "tmp": tmp_path}


def test_expand_cells_merge_order_and_placeholders(env):
    cfg = rm.load_config(env["cfg_path"])
    cells = rm.expand_cells(cfg)
    ids = [c.cell_id for c in cells]
    # A: plain×2 + guided×2；B: plain×2（guided 限 A）；路線在最外層
    assert ids == ["park__A__plain__s1", "park__A__plain__s2", "park__A__guided__s1", "park__A__guided__s2",
                   "park__B__plain__s1", "park__B__plain__s2"]
    a = cells[0].params
    assert a["prompt"] == "a bunny, in a park, line art {keep}"  # 未知大括號原樣保留
    assert a["steps"] == 12 and a["seed"] == 1
    assert cells[4].params["cfg"] == 2.0 and "steps" not in cells[4].params
    assert cells[2].postprocess["close_kernel"] == 3 and cells[0].postprocess.get("close_kernel") is None


@pytest.mark.parametrize("patch, msg", [
    ({"routes": None}, "routes"),
    ({"seeds": []}, "seeds"),
    ({"seeds": [1, "2"]}, "seeds"),
    ({"routes": {"A": {}}}, "workflow"),
])
def test_config_errors(tmp_path, patch, msg):
    base = {"routes": {"A": {"workflow": "x"}}, "seeds": [1], "lineart_methods": {"p": {}}, "scenes": {"s": {}}}
    base.update(patch)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(base))
    with pytest.raises(rm.ConfigError, match=msg):
        rm.load_config(p)


def test_dry_run(env, capsys):
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "共 6 格，0 格有問題" in out
    assert not env["fake"].prompts


def test_full_run_outputs_csv_html_and_summary(env, capsys):
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"])])
    out = capsys.readouterr().out
    assert code == 0, out
    run_dir = next((env["tmp"] / "results").iterdir())
    rows = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))
    assert len(rows) == 6 and all(r["status"] == "ok" for r in rows)
    r = rows[0]
    assert r["workflow"] == "tiny" and len(r["workflow_hash"]) == 64 and r["models"] == "base.safetensors"
    params = json.loads(r["params_json"])
    assert params["seed"] == 1 and params["ref_image"].startswith("coloring_books/")
    assert int(r["closed_region_count"]) >= 1 and r["elapsed_s"]
    assert (run_dir / r["raw_image"]).is_file() and (run_dir / r["processed_image"]).is_file()

    fake = env["fake"]
    assert len(fake.uploads) == 1  # 同一張參考圖只上傳一次
    assert fake.freed == 1  # A → B 換路線時釋放一次
    sent = fake.prompts[0]["prompt"]
    assert sent["5"]["inputs"]["seed"] == 1 and sent["5"]["inputs"]["steps"] == 12

    page = (run_dir / "report.html").read_text(encoding="utf-8")
    assert page.count("data-field='consistency_score'") == 6
    assert "匯出分數 CSV" in page and "processed/park__A__plain__s1.png" in page

    meta = json.loads((run_dir / "run.json").read_text())
    assert meta["workflows"]["A"]["version"]

    (run_dir / "scores.csv").write_text("cell_id,consistency_score,notes\npark__A__plain__s1,4,\n"
                                        "park__A__plain__s2,5,\n", encoding="utf-8")
    assert rm.main(["summarize", str(run_dir)]) == 0
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "| A | plain | 2/2 | 4.5 ± 0.707 (n=2)" in summary
    assert (run_dir / "summary.csv").is_file()


def test_failures_recorded_and_resume(env, capsys):
    fake = env["fake"]
    fake.mode = "error"
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--limit", "2"])
    assert code == 1
    run_dir = next((env["tmp"] / "results").iterdir())
    rows = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))
    assert all(r["status"] == "error" and "CUDA out of memory" in r["error"] for r in rows)
    assert "失敗：" in (run_dir / "report.html").read_text(encoding="utf-8")

    fake.mode = "ok"
    n_before = len(fake.prompts)
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--limit", "2",
                    "--resume", str(run_dir)])
    assert code == 0
    assert len(fake.prompts) - n_before == 2
    # 再接續一次：全部已成功，不再送出
    rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--limit", "2",
             "--resume", str(run_dir)])
    assert len(fake.prompts) - n_before == 2


def test_workflow_failing_preflight_is_skipped(env, capsys):
    fake = env["fake"]
    del fake.object_info["VAEDecode"]
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--route", "B"])
    assert code == 1
    run_dir = next((env["tmp"] / "results").iterdir())
    rows = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))
    assert {r["status"] for r in rows} == {"skipped"}
    assert not fake.prompts


def test_missing_image_is_a_cell_error(env):
    cfg = yaml.safe_load(env["cfg_path"].read_text())
    cfg["character"]["reference_image"] = str(env["tmp"] / "nope.png")
    env["cfg_path"].write_text(yaml.safe_dump(cfg, sort_keys=False))
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--limit", "1"])
    assert code == 1
    run_dir = next((env["tmp"] / "results").iterdir())
    row = next(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))
    assert row["status"] == "error" and "找不到圖片" in row["error"]


def test_example_config_expands():
    cfg = rm.load_config(REPO / "experiments" / "configs" / "phase1_example.yaml")
    cells = rm.expand_cells(cfg)
    assert len(cells) == 1 * 2 * 2 * 3  # 只剩 Route A：2 線稿方式 × 2 場景 × 3 seeds
    assert {c.route for c in cells} == {"A"}
    assert all("{" not in str(v) for c in cells for v in c.params.values())


def test_preflight_only_checks_selected_routes(env, capsys):
    wf_dir = Path(yaml.safe_load(env["settings"].read_text())["paths"]["workflows_dir"])
    (wf_dir / "tiny_b" / "workflow_api.json").unlink()  # Route B 尚未匯出
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--route", "A", "--limit", "1"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "tiny_b" not in out and "結果：全部通過" in out


def test_stops_when_comfy_goes_down(env, capsys):
    fake = env["fake"]
    real_handler = fake.handler

    def die_on_second_prompt(request):
        if request.url.path == "/prompt" and len(fake.prompts) == 1:
            fake.dead = True
        return real_handler(request)

    fake.handler = die_on_second_prompt
    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"])])
    out = capsys.readouterr().out
    assert code == 1
    assert "已無法連線，停止執行" in out and "--resume" in out
    run_dir = next((env["tmp"] / "results").iterdir())
    rows = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))
    # 第 1 格成功、第 2 格失敗後立即停止，剩下 4 格沒有被記成失敗
    assert [r["status"] for r in rows] == ["ok", "error"]
    assert (run_dir / "report.html").is_file()

    fake.dead = False
    fake.handler = real_handler
    assert rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]),
                    "--resume", str(run_dir)]) == 0
    rows = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))
    assert len(rows) == 6 and all(r["status"] == "ok" for r in rows)


def test_unreachable_comfy_leaves_no_run_dir(env):
    env["fake"].dead = True
    assert rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"])]) == 1
    assert not (env["tmp"] / "results").exists() or not any((env["tmp"] / "results").iterdir())


# ---- Route C：擷取線稿、共用生成結果、對既有結果重新擷取 --------------------------------

def _add_extract_methods(env):
    cfg = yaml.safe_load(env["cfg_path"].read_text())
    plain = cfg["lineart_methods"]["plain"]
    # 與 plain 生成參數完全相同、只差擷取方式 → 不應該重新生成
    cfg["lineart_methods"]["plain_luma"] = {**plain, "routes": ["A"], "extract": {"method": "luma", "threshold": 100}}
    cfg["lineart_methods"]["plain_xdog"] = {**plain, "routes": ["A"], "extract": {"method": "xdog"}}
    env["cfg_path"].write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))


def test_extract_config_is_validated(env):
    cfg = yaml.safe_load(env["cfg_path"].read_text())
    cfg["lineart_methods"]["plain"]["extract"] = {"method": "magic"}
    env["cfg_path"].write_text(yaml.safe_dump(cfg, sort_keys=False))
    with pytest.raises(rm.ConfigError, match="lineart_methods.plain.extract"):
        rm.load_config(env["cfg_path"])


def test_extract_and_generation_reuse(env, capsys):
    _add_extract_methods(env)
    cells = rm.expand_cells(rm.load_config(env["cfg_path"]))
    assert len(cells) == 10
    luma = next(c for c in cells if c.lineart == "plain_luma")
    assert luma.extract == {"method": "luma", "threshold": 100}

    code = rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"])])
    out = capsys.readouterr().out
    assert code == 0, out
    # A: plain×2 + guided×2，B: plain×2；plain_luma / plain_xdog 共用 A plain 的生成結果
    assert len(env["fake"].prompts) == 6
    run_dir = next((env["tmp"] / "results").iterdir())
    rows = {r["cell_id"]: r for r in csv.DictReader(open(run_dir / "results.csv", encoding="utf-8"))}
    assert len(rows) == 10 and all(r["status"] == "ok" for r in rows.values())
    src, reused = rows["park__A__plain__s1"], rows["park__A__plain_luma__s1"]
    assert src["extract_method"] == "none" and src["gen_source"] == ""
    assert reused["gen_source"] == "park__A__plain__s1" and reused["raw_image"] == src["raw_image"]
    assert reused["extract_method"] == "luma" and json.loads(reused["extract_params"])["threshold"] == 100
    assert reused["extract_s"] != "" and reused["processed_image"] != src["processed_image"]
    assert (run_dir / reused["processed_image"]).is_file()

    # 接續時也能從 results.csv 找回已生成的圖：刪掉一格 xdog 再跑，不應送出新的 prompt
    del rows["park__A__plain_xdog__s2"]
    from experiments.metrics import write_csv
    write_csv(list(rows.values()), run_dir / "results.csv", rm.RESULT_FIELDS)
    n = len(env["fake"].prompts)
    assert rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--resume", str(run_dir)]) == 0
    assert len(env["fake"].prompts) == n
    rows = {r["cell_id"]: r for r in csv.DictReader(open(run_dir / "results.csv", encoding="utf-8"))}
    assert rows["park__A__plain_xdog__s2"]["gen_source"] == "park__A__plain__s2"


def test_reextract_existing_run(env, tmp_path, capsys):
    assert rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--route", "A"]) == 0
    src_dir = next((env["tmp"] / "results").iterdir())
    n_prompts = len(env["fake"].prompts)

    rx = {
        "name": "rx",
        "route": "C0",
        "output_dir": str(tmp_path / "rx_results"),
        "postprocess": {"target_width": 180, "target_height": 240, "line_width_px": 3, "min_fragment_area": 4,
                        "min_region_area": 50},
        "extract_methods": {"none": {"method": "none"}, "luma": {"method": "luma"}, "xdog": {"method": "xdog"}},
    }
    rx_path = tmp_path / "rx.yaml"
    rx_path.write_text(yaml.safe_dump(rx))
    code = rm.main(["reextract", str(src_dir), "--config", str(rx_path)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert len(env["fake"].prompts) == n_prompts  # 完全不碰 ComfyUI

    out_dir = next((tmp_path / "rx_results").iterdir())
    rows = list(csv.DictReader(open(out_dir / "results.csv", encoding="utf-8")))
    assert len(rows) == 4 * 3 and all(r["status"] == "ok" for r in rows)
    r = next(r for r in rows if r["cell_id"] == "park__C0__plain-luma__s1")
    assert r["route"] == "C0" and r["lineart"] == "plain-luma" and r["extract_method"] == "luma"
    assert r["gen_source"] == f"{src_dir.name}/park__A__plain__s1"
    assert (out_dir / r["raw_image"]).is_file() and (out_dir / r["processed_image"]).is_file()
    assert r["workflow_version"]  # 來源的生成紀錄要保留
    page = (out_dir / "report.html").read_text(encoding="utf-8")
    assert page.count("data-field='consistency_score'") == 12
    meta = json.loads((out_dir / "run.json").read_text())
    assert meta["source_run"] == str(src_dir.resolve()) and meta["config"]["extract_methods"]["luma"]


def test_reextract_rejects_bad_method(env, tmp_path):
    rx_path = tmp_path / "rx.yaml"
    rx_path.write_text(yaml.safe_dump({"extract_methods": {"x": {"method": "nope"}}}))
    assert rm.main(["reextract", str(tmp_path), "--config", str(rx_path)]) == 2


def test_route_c1_config_expands():
    cfg = rm.load_config(REPO / "experiments" / "configs" / "phase1_route_c1.yaml")
    cells = rm.expand_cells(cfg)
    assert len(cells) == 6 * 3 * 3
    c = cells[0]
    assert c.extract["method"] in ("luma", "xdog")
    assert "color" not in c.params["negative_prompt"].replace("colors", "")  # 不可再壓制顏色
    # luma 與 xdog 兩個變體的生成參數完全相同 → 只會生成一次
    a = next(x for x in cells if x.lineart == "ip055_luma" and x.scene == "park" and x.seed == 101)
    b = next(x for x in cells if x.lineart == "ip055_xdog" and x.scene == "park" and x.seed == 101)
    assert a.params == b.params and a.extract != b.extract


def test_route_c2_config_expands():
    cfg = rm.load_config(REPO / "experiments" / "configs" / "phase1_route_c2.yaml")
    cells = rm.expand_cells(cfg)
    assert len(cells) == 12 * 3 * 3
    by = {(c.lineart, c.scene, c.seed): c for c in cells}
    k = by[("scene_cn07_luma", "kitchen", 101)]
    assert k.params["structure_image"].endswith("pose/kitchen_baking.png")
    assert k.params["controlnet_strength"] == 0.7 and k.params["controlnet_type"] == "hed/pidi/scribble/ted"
    assert k.params["negative_prompt"].startswith("plain background")
    w = by[("white_none_xdog", "kitchen", 101)]
    assert w.params["controlnet_strength"] == 0.0 and "plain white background" in w.params["prompt"]
    assert not w.params["negative_prompt"].startswith("plain background")
    # luma / xdog 共用生成 → 54 組不同的生成參數
    assert len({json.dumps(c.params, sort_keys=True) for c in cells}) == 54


def test_reextract_skips_shared_generations(env, tmp_path, capsys):
    _add_extract_methods(env)
    assert rm.main(["run", str(env["cfg_path"]), "--settings", str(env["settings"]), "--route", "A"]) == 0
    src_dir = next((env["tmp"] / "results").iterdir())  # A: plain/guided/plain_luma/plain_xdog × 2 seeds，生成 4 張
    rx_path = tmp_path / "rx.yaml"
    rx_path.write_text(yaml.safe_dump({"output_dir": str(tmp_path / "rx"), "extract_methods": {"luma": {"method": "luma"}},
                                       "postprocess": {"target_width": 180, "target_height": 240}}))
    assert rm.main(["reextract", str(src_dir), "--config", str(rx_path)]) == 0
    rows = list(csv.DictReader(open(next((tmp_path / "rx").iterdir()) / "results.csv", encoding="utf-8")))
    assert sorted(r["lineart"] for r in rows) == ["guided-luma", "guided-luma", "plain-luma", "plain-luma"]


def test_route_c3_config_expands():
    cfg = rm.load_config(REPO / "experiments" / "configs" / "phase1_route_c3.yaml")
    cells = rm.expand_cells(cfg)
    assert len(cells) == 9 * 3 * 3
    assert {c.extract["method"] for c in cells} == {"luma", "xdog", "hybrid"}
    assert all(c.params["structure_image"].endswith("_props.png") for c in cells)
    assert all((REPO / c.params["structure_image"]).is_file() for c in cells)
    assert len({json.dumps(c.params, sort_keys=True) for c in cells}) == 27


def test_route_c4_age_config_expands():
    cfg = rm.load_config(REPO / "experiments" / "configs" / "phase1_route_c4_age.yaml")
    cells = rm.expand_cells(cfg)
    assert len(cells) == 4 * 3 * 3
    by = {(c.lineart, c.scene, c.seed): c for c in cells}
    a, b = by[("age2_6", "park", 101)], by[("age2_6_bold", "park", 101)]
    assert a.params == b.params  # 粗線版共用同一張彩圖
    assert b.postprocess["line_width_px"] == 16 and a.postprocess["line_width_px"] == 11
    assert "toddlers" in a.params["prompt"] and "intricate details" in a.params["negative_prompt"]
    assert by[("age6_12", "park", 101)].params["controlnet_strength"] == 0.4
    assert len({json.dumps(c.params, sort_keys=True) for c in cells}) == 27
