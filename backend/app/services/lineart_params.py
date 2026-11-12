"""產品線稿的固定演算法參數（render_base／render_lineart 專用，不依賴 LineartParams／實驗方法表的預設值）。

這些值決定線稿的每一個像素。改任何一個，都要把 lineart_render.RENDER_VERSION 加 1（QAThresholds 則是
qa.QA_VERSION），已存的線稿、預覽和 QA 結果才會失效重算；lineart_key 不含這些值（舊頁面的 key 不能變）。
tests/test_lineart_render.py 的快照測試會在值變動時失敗，提醒你加版本號。
"""

from __future__ import annotations

from typing import Any

# xdog（eXtended Difference of Gaussians）；sigma 依「線條細節」而不同（lineart_render.DETAIL_SIGMA）
XDOG_K = 1.6
XDOG_P = 20.0
XDOG_EPS = 0.02
XDOG_PHI = 10.0

# 放大到內容區：等比縮放、白邊補滿，Lanczos 內插（縮小時一律 area）；二值化用 Otsu
FIT = "contain"
INTERPOLATION = "lanczos"
BINARIZE = "otsu"

# 清理：移除小碎片、形態學閉運算封閉缺口
MIN_FRAGMENT_AREA = 64
CLOSE_KERNEL = 7
CLOSE_ITERATIONS = 1


def as_dict() -> dict[str, Any]:
    return {
        "xdog": [XDOG_K, XDOG_P, XDOG_EPS, XDOG_PHI],
        "fit": FIT, "interpolation": INTERPOLATION, "binarize": BINARIZE,
        "min_fragment_area": MIN_FRAGMENT_AREA, "close_kernel": CLOSE_KERNEL, "close_iterations": CLOSE_ITERATIONS,
    }
