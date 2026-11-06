import io

import numpy as np
from PIL import Image

from app.services.export import build_pdf, compose_page
from app.services.layout import PaperSize, PrintMode, compute_layout


def test_compose_home_a4():
    lay = compute_layout(PaperSize.A4, PrintMode.HOME)
    assert lay.page_px == (2480, 3508)
    ink = np.zeros(lay.content_px[::-1], dtype=bool)
    ink[0, 0] = True
    im = compose_page(ink, lay)
    assert im.mode == "1" and im.size == (2480, 3508)
    a = np.asarray(im)  # True = white
    assert not a[118, 118]
    assert a.sum() == a.size - 1


def test_pdf_mediabox():
    for mode, box in ((PrintMode.HOME, b"/MediaBox [ 0 0 595.2 841.92 ]"),
                      (PrintMode.PRINT_SHOP, b"/MediaBox [ 0 0 612.24 858.96 ]")):
        lay = compute_layout(PaperSize.A4, mode)
        ink = np.zeros(lay.content_px[::-1], dtype=bool)
        pages = [compose_page(ink, lay) for _ in range(2)]
        pdf = build_pdf(pages)
        assert pdf.startswith(b"%PDF") and box in pdf
        assert pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 2
        assert pdf.count(box) == 2
