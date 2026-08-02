import struct

from scripts.visual_review_worker import _png_dimensions


def test_png_dimensions_accepts_a_valid_non_empty_png_header(tmp_path):
    image = tmp_path / "desktop.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1440, 1000)
    )

    assert _png_dimensions(image) == (1440, 1000)


def test_png_dimensions_rejects_non_png_content(tmp_path):
    image = tmp_path / "desktop.png"
    image.write_text("not an image", encoding="utf-8")

    assert _png_dimensions(image) is None
