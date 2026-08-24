"""PDF scans are containers holding one comic cover per page. Each page renders to
an image and enters the pipeline as its own photo — nothing downstream learns what
a PDF is."""

import pymupdf
import pytest

from comicload.errors import ComicloadError
from comicload.ingestion.pdf import pages_png
from comicload.ingestion.photos import LocalFolderPhotoSource


def _pdf_bytes(pages: int = 1, shade: float = 0.2) -> bytes:
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=200, height=300)
        fill = (0.9, shade, (index + 1) / (pages + 1))
        page.draw_rect(pymupdf.Rect(20, 20, 180, 280), fill=fill)
    return doc.tobytes()


def test_every_page_renders_to_png_bytes():
    pages = pages_png(_pdf_bytes(pages=3))
    assert len(pages) == 3
    assert all(page.startswith(b"\x89PNG") for page in pages)


def test_a_broken_pdf_is_a_clear_error():
    with pytest.raises(ComicloadError, match="could not be read"):
        pages_png(b"not a pdf at all")


def test_photo_source_yields_one_photo_per_pdf_page(tmp_path):
    (tmp_path / "longbox.pdf").write_bytes(_pdf_bytes(pages=3))
    photos = list(LocalFolderPhotoSource(tmp_path).photos())
    assert [p.filename for p in photos] == [
        "longbox.pdf (page 1 of 3)",
        "longbox.pdf (page 2 of 3)",
        "longbox.pdf (page 3 of 3)",
    ]
    assert all(p.data.startswith(b"\x89PNG") for p in photos)


def test_a_single_page_pdf_keeps_its_plain_filename(tmp_path):
    (tmp_path / "scan.pdf").write_bytes(_pdf_bytes(pages=1))
    photos = list(LocalFolderPhotoSource(tmp_path).photos())
    assert [p.filename for p in photos] == ["scan.pdf"]


def test_identical_pdfs_collapse_page_by_page(tmp_path):
    (tmp_path / "a.pdf").write_bytes(_pdf_bytes(pages=2))
    (tmp_path / "b.pdf").write_bytes(_pdf_bytes(pages=2))
    photos = list(LocalFolderPhotoSource(tmp_path).photos())
    assert len(photos) == 2, "the same two pages scanned twice are two comics, not four"


def test_an_unreadable_pdf_does_not_stop_the_folder(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"garbage")
    (tmp_path / "good.jpg").write_bytes(b"jpeg bytes")
    photos = list(LocalFolderPhotoSource(tmp_path).photos())
    assert [p.filename for p in photos] == ["good.jpg"]
