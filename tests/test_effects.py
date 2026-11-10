from pathlib import Path
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageColor
from dthr.effects import process
from dthr.schema import Processing
from dthr.storage import png


@pytest.fixture(params=["gradient", "lines", "noise", "photo"])
def source(request):
    if request.param == "photo":
        # Lav Varshney's CC0 photograph, from scikit-image v0.25.2.
        with Image.open(Path(__file__).parent / "fixtures/camera.png") as image:
            return image.copy()
    if request.param == "gradient":
        return Image.linear_gradient("L").resize((61, 97))
    if request.param == "lines":
        im = Image.new("RGB", (27, 111))
        draw = ImageDraw.Draw(im)
        for y in range(0, 111, 4):
            draw.line((0, y, 26, y + 10), fill="white")
        return im
    return Image.fromarray(
        np.random.default_rng(4).integers(256, size=(73, 31, 3), dtype=np.uint8)
    )


@pytest.mark.parametrize("effect", ["pixel", "floyd", "bayer", "threshold", "grunge"])
@pytest.mark.parametrize("strength", [0, 55, 100])
def test_effects(source, effect, strength):
    original = source.tobytes()
    settings = Processing(
        effect=effect, strength=strength, foreground="#0102ab", background="#fea700"
    )
    im = process(source, (13, 89), settings)
    assert im.size == (13, 89)
    assert set(im.convert("RGB").getdata()) <= {
        ImageColor.getrgb(settings.foreground),
        ImageColor.getrgb(settings.background),
    }
    assert png(im) == png(process(source, (13, 89), settings))
    assert source.tobytes() == original


def test_panel_blocks():
    source = Image.linear_gradient("L").rotate(45)
    result = np.asarray(process(source, (220, 900), Processing()))
    for y in range(0, 900, 7):
        for x in range(0, 220, 7):
            assert np.all(result[y : y + 7, x : x + 7] == result[y, x])


def test_zero_strength_and_seeded_distress():
    im = Image.linear_gradient("L")
    size = (59, 127)
    base = process(im, size, Processing(effect="threshold", strength=0))
    for effect in ("pixel", "bayer", "floyd", "grunge"):
        assert (
            process(im, size, Processing(effect=effect, strength=0)).tobytes()
            == base.tobytes()
        )
    a = process(im, size, Processing(effect="grunge", strength=100, texture_seed=55))
    b = process(im, size, Processing(effect="grunge", strength=100, texture_seed=56))
    assert a.tobytes() != b.tobytes()


def test_floyd_reference():
    from dthr.effects import diffusion

    values = np.random.default_rng(475).integers(256, size=(43, 33)).astype(float)
    buf = values.copy()
    result = np.zeros_like(buf)
    for y in range(buf.shape[0]):
        for x in range(buf.shape[1]):
            bit = buf[y, x] >= 128
            result[y, x] = bit
            err = (buf[y, x] - 255 * int(bit)) / 16
            for dy, dx, n in ((0, 1, 7), (1, -1, 3), (1, 0, 5), (1, 1, 1)):
                if y + dy < buf.shape[0] and 0 <= x + dx < buf.shape[1]:
                    buf[y + dy, x + dx] += n * err
    assert np.array_equal(result, diffusion(values, 128, 1))
