"""Render each page of a PDF scan to PNG bytes.

A PDF is a container holding one comic cover per page, not an image format. Doing
the conversion at the photo source keeps every signal ignorant of PDFs — they only
ever see image bytes.
"""

from __future__ import annotations

import pymupdf

from comicload.errors import ComicloadError

# 6x scale ≈ 432 DPI. Verified against a real scan: at 2.5x (≈180 DPI) the main
# UPC still read fine but the EAN-5 supplement — which tells one issue of a series
# from another when the publisher reuses a single UPC — was unreadable on every
# page tested, 0 of 6. 6x recovered all of them. Costs real time and memory over
# 2.5x, but a barcode that resolves to 25 possible issues instead of 1 is a worse
# trade than a slower scan.
_RENDER_SCALE = 6.0


def pages_png(pdf_bytes: bytes) -> list[bytes]:
    """Render every page to PNG bytes, in page order.

    Raises ComicloadError for anything unreadable — the caller decides whether that
    stops the run or skips the file.
    """
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            if document.page_count == 0:
                raise ComicloadError("this PDF could not be read: it has no pages")
            matrix = pymupdf.Matrix(_RENDER_SCALE, _RENDER_SCALE)
            pages: list[bytes] = []
            for page in document:
                png: bytes = page.get_pixmap(matrix=matrix).tobytes("png")
                pages.append(png)
            return pages
    except ComicloadError:
        raise
    except Exception as exc:
        raise ComicloadError(f"this PDF could not be read: {exc}") from exc
