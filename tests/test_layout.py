import pytest

from app.services.layout import PaperSize, PrintMode, compute_layout


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
