import pytest

from app.services.layout import PaperSize, PrintMode, compute_layout, ratio_group


@pytest.mark.parametrize("size, mode, content_px, gen", [
    (PaperSize.A4, PrintMode.HOME, (2244, 3272), (832, 1216)),
    (PaperSize.A4, PrintMode.PRINT_SHOP, (2551, 3579), (864, 1216)),
    (PaperSize.A5, PrintMode.HOME, (1512, 2244), (832, 1216)),
    (PaperSize.B5, PrintMode.PRINT_SHOP, (2220, 3106), (864, 1216)),
    (PaperSize.LETTER, PrintMode.HOME, (2314, 3064), (896, 1152)),
    (PaperSize.LETTER, PrintMode.PRINT_SHOP, (2621, 3371), (896, 1152)),
    (PaperSize.SQUARE_21, PrintMode.HOME, (2244, 2244), (1024, 1024)),
])
def test_compute_layout(size, mode, content_px, gen):
    lay = compute_layout(size, mode)
    assert lay.content_px == content_px and lay.generation_size == gen


def test_home_has_margins_print_shop_has_bleed_and_safe_area():
    home = compute_layout(PaperSize.A4, PrintMode.HOME)
    shop = compute_layout(PaperSize.A4, PrintMode.PRINT_SHOP)
    assert home.content_mm == (190, 277) and home.safe_inset_mm is None
    assert shop.content_mm == (216, 303) and shop.safe_inset_mm == 8  # 出血 3 + 安全區 5


@pytest.mark.parametrize("size, mode, page_px, offset, trim, safe", [
    (PaperSize.A4, PrintMode.HOME, (2480, 3508), (118, 118), None, None),
    (PaperSize.A4, PrintMode.PRINT_SHOP, (2551, 3579), (0, 0), (35, 35, 2515, 3543), (94, 94, 2456, 3484)),
    (PaperSize.B5, PrintMode.HOME, (2150, 3035), (118, 118), None, None),
    (PaperSize.LETTER, PrintMode.PRINT_SHOP, (2621, 3371), (0, 0), (35, 35, 2585, 3335), (94, 94, 2526, 3276)),
    (PaperSize.SQUARE_21, PrintMode.HOME, (2480, 2480), (118, 118), None, None),
])
def test_page_geometry(size, mode, page_px, offset, trim, safe):
    lay = compute_layout(size, mode)
    assert lay.page_px == page_px
    assert lay.content_offset_px == offset
    assert lay.trim_box_px == trim
    assert lay.safe_box_px == safe


def test_ratio_groups():
    assert {ratio_group(s) for s in (PaperSize.A4, PaperSize.A5, PaperSize.B5)} == {"iso"}
    assert ratio_group(PaperSize.LETTER) == "letter" and ratio_group(PaperSize.SQUARE_21) == "square"


def test_layout_exposes_ratio_group_and_paper_mm():
    lay = compute_layout(PaperSize.LETTER, PrintMode.HOME)
    assert lay.ratio_group == "letter"
    assert lay.paper_mm == (215.9, 279.4)
