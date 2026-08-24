"""PDF scans are containers: page one rendered to an image is just another way of
getting cover pixels. Nothing downstream learns what a PDF is."""

import pymupdf
import pytest

from comicload.core.errors import ComicloadError
from comicload.infra.pdf import first_page_png
from comicload.infra.photos import LocalFolderPhotoSource


def _pdf_bytes(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=200, height=300)
        page.draw_rect(pymupdf.Rect(20, 20, 180, 280), fill=(0.8, 0.2, 0.2))
    return doc.tobytes()


def test_first_page_renders_to_png_bytes():
    png = first_page_png(_pdf_bytes())
    assert png.startswith(b"\x89PNG")


def test_only_the_first_page_is_used():
    assert first_page_png(_pdf_bytes(pages=3)).startswith(b"\x89PNG")


def test_a_broken_pdf_is_a_clear_error():
    with pytest.raises(ComicloadError, match="could not be read"):
        first_page_png(b"not a pdf at all")


def test_photo_source_yields_pdf_pages_as_images(tmp_path):
    (tmp_path / "scan.pdf").write_bytes(_pdf_bytes())
    photos = list(LocalFolderPhotoSource(tmp_path).photos())
    assert [p.filename for p in photos] == ["scan.pdf"]
    assert photos[0].data.startswith(b"\x89PNG"), "PDF must arrive as image bytes"


def test_identical_pdfs_collapse_to_one_photo(tmp_path):
    (tmp_path / "a.pdf").write_bytes(_pdf_bytes())
    (tmp_path / "b.pdf").write_bytes(_pdf_bytes())
    ids = {p.id for p in LocalFolderPhotoSource(tmp_path).photos()}
    assert len(ids) == 1


def test_an_unreadable_pdf_does_not_stop_the_folder(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"garbage")
    (tmp_path / "good.jpg").write_bytes(b"jpeg bytes")
    photos = list(LocalFolderPhotoSource(tmp_path).photos())
    assert [p.filename for p in photos] == ["good.jpg"]
