"""Render each page of a PDF scan to PNG bytes.

A PDF is a container holding one comic cover per page, not an image format. Doing
the conversion at the photo source keeps every signal ignorant of PDFs — they only
ever see image bytes.
"""

from __future__ import annotations

import pymupdf

from comicload.domain.errors import ComicloadError

# 6x scale ≈ 432 DPI. The main UPC reads at far less, but the EAN-5 supplement —
# which distinguishes issues sharing a UPC — needs the extra resolution on real
# scanner output. Pages are processed one at a time, so peak memory stays modest.
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
