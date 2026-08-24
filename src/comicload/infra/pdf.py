"""Render the first page of a PDF scan to PNG bytes.

A PDF is a container, not an image format: page one rendered at scan resolution is
the cover. Doing the conversion at the photo source keeps every signal ignorant of
PDFs — they only ever see image bytes.
"""

from __future__ import annotations

import pymupdf

from comicload.core.errors import ComicloadError

# 2x scale ≈ 144 DPI — enough for zbar to read a barcode without ballooning memory.
_RENDER_SCALE = 2.0


def first_page_png(pdf_bytes: bytes) -> bytes:
    """Render page one to PNG bytes. Raises ComicloadError for anything unreadable."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            if document.page_count == 0:
                raise ComicloadError("this PDF could not be read: it has no pages")
            page = document[0]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(_RENDER_SCALE, _RENDER_SCALE))
            png: bytes = pixmap.tobytes("png")
            return png
    except ComicloadError:
        raise
    except Exception as exc:
        raise ComicloadError(f"this PDF could not be read: {exc}") from exc
