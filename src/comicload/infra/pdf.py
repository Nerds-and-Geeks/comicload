"""Render each page of a PDF scan to PNG bytes.

A PDF is a container holding one comic cover per page, not an image format. Doing
the conversion at the photo source keeps every signal ignorant of PDFs — they only
ever see image bytes.
"""

from __future__ import annotations

import pymupdf

from comicload.core.errors import ComicloadError

# 2x scale ≈ 144 DPI — enough for zbar to read a barcode without ballooning memory.
_RENDER_SCALE = 2.0


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
