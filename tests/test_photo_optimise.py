"""Big uploaded photos are shrunk to web size; small ones are kept exactly as they came."""

import io
import os

from PIL import Image

from app.services import media_service


def noisy(size, fmt="PNG", **save):
    """A photo-like image that does not compress well (random pixels), like a camera file."""
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    b = io.BytesIO()
    img.save(b, fmt, **save)
    return b.getvalue()


def test_a_big_png_becomes_a_small_webp_that_fits_the_web_size():
    data = noisy((3000, 2000))
    assert len(data) > 5 * 1024 * 1024  # like the 7 MB render that slowed the shop
    ext, out, ctype = media_service.optimise_image("png", data)
    assert (ext, ctype) == ("webp", "image/webp")
    assert len(out) < len(data) / 3
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "WEBP"
        assert max(img.size) == media_service.WEB_MAX_SIDE
        assert img.size == (2000, 1333)  # same proportions


def test_a_small_photo_is_kept_exactly_as_uploaded():
    b = io.BytesIO()
    Image.new("RGB", (64, 48), "teal").save(b, "JPEG")
    data = b.getvalue()
    assert media_service.optimise_image("jpg", data) == ("jpg", data, "image/jpeg")


def test_a_big_jpeg_with_a_gps_location_loses_it_and_turns_upright():
    img = Image.frombytes("RGB", (2400, 1200), os.urandom(2400 * 1200 * 3))
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees" (a phone held upright)
    exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0)}  # GPS block
    b = io.BytesIO()
    img.save(b, "JPEG", quality=95, exif=exif)
    ext, out, _ = media_service.optimise_image("jpg", b.getvalue())
    assert ext == "webp"
    with Image.open(io.BytesIO(out)) as res:
        assert (
            res.size[1] > res.size[0]
        )  # turned upright: 1200 wide, 2400 tall -> scaled to fit
        assert max(res.size) == media_service.WEB_MAX_SIDE
        assert not res.getexif().get(0x8825)  # no location left


def test_an_image_with_transparency_keeps_it():
    img = Image.new("RGBA", (2600, 1000), (200, 100, 50, 0))
    b = io.BytesIO()
    img.save(b, "PNG")
    ext, out, _ = media_service.optimise_image("png", b.getvalue())
    with Image.open(io.BytesIO(out)) as res:
        assert res.mode == "RGBA" and res.getpixel((5, 5))[3] == 0


def test_anything_that_cannot_be_read_is_returned_unchanged():
    junk = b"not an image at all" * 50000
    assert media_service.optimise_image("png", junk) == ("png", junk, "image/png")
