import io

from PIL import Image

from comicload.cli.render import cover_lines


def _png(width=60, height=90) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (180, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_ansi_fallback_renders_bounded_truecolor_rows(monkeypatch):
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("LC_TERMINAL", raising=False)
    out = cover_lines(_png(), width=20)
    lines = out.split("\n")
    assert all("▀" in line for line in lines)
    assert "38;2;" in out and "48;2;" in out
    assert all(line.endswith("\033[0m") for line in lines)


def test_iterm_gets_the_inline_image_protocol(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    out = cover_lines(_png())
    assert out.startswith("\033]1337;File=inline=1")
