"""Render each page of a PDF scan to PNG bytes.

A PDF is a container holding one comic cover per page, not an image format. Doing
the conversion at the photo source keeps every signal ignorant of PDFs — they only
ever see image bytes.
"""

from __future__ import annotations

import pymupdf

from comicload.errors import ComicloadError

# 2.5x scale ≈ 180 DPI. Extremely fast rendering and lightweight memory while
# remaining sharp enough for the main UPC and small EAN-5 supplement barcodes.
_RENDER_SCALE = 2.5


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
