# 實驗用輸入圖

- `blank_white.png`：全白圖（864×1216）。不需要結構控制的線稿方式用它當 structure_image。
- `structure_sketch.png`：程式產生的簡單結構草圖（白底黑線：鞦韆架 + 角色位置），僅供第一次跑通流程。
- `character_ref.png`：**請自行放入**角色參考圖（建議正面、全身、白底、單一角色）。

`character_ref.png` 目前為範例：以 SDXL Base 1.0 文生圖產生（832×1216，seed 11，dpmpp_2m karras，30 steps，cfg 6），
prompt：character reference, single full body front view of a cute small round bunny girl, long floppy ears, big round eyes,
wearing blue and white striped overalls, standing, children's picture book illustration, flat colors, simple clean shapes,
plain white background, centered, whole body visible。可直接換成你的角色圖。

`character_ref_lineart.png`：character_ref.png 的線稿版（Route A 第二輪 ref_lineart 變體用）。產生方式：
灰階 → GaussianBlur 3×3 → Canny(30, 90) → close_gaps(3) → remove_small_fragments(30) → unify_line_width(4)
（函式皆在 app.services.lineart_post）。專案的二值化後處理用在彩色參考圖上會丟失瀏海與臉部細節，所以改用色塊邊緣。

`pose/`：Route C 第二輪（C2）用的姿勢／構圖草圖（白底黑線，給 ControlNet Union scribble 模式）。
由 `experiments/pose_sketches.py` 產生（`python experiments/pose_sketches.py`）；改了產生器要重新產生，
`tests/test_pose_sketches.py` 會檢查檔案與產生器輸出一致。程式畫的火柴人太簡陋，C2 發現會把角色比例拉壞，
換成手繪草圖效果應會更好。
