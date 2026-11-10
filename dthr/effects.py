"""CPU effects. All filtering occurs before the final binary palette lookup."""

import numpy as np
from PIL import Image, ImageColor, ImageEnhance, ImageOps
from .schema import Processing

NEAREST = Image.Resampling.NEAREST


def fitted(image, size, fit):
    # Transparent input is composited on black (the zero-luminance paper index).
    rgba = image.convert("RGBA")
    base = Image.new("RGB", rgba.size)
    base.paste(rgba, mask=rgba.getchannel("A"))
    if fit == "contain":
        return ImageOps.pad(base, size, method=Image.Resampling.LANCZOS, color="black")
    return ImageOps.fit(base, size, method=Image.Resampling.LANCZOS)


def luminance(image, p):
    gray = image.convert("L")
    for factor, enhancer in [
        (p.sharpness, ImageEnhance.Sharpness),
        (p.brightness, ImageEnhance.Brightness),
        (p.contrast, ImageEnhance.Contrast),
    ]:
        if factor != 1:
            gray = enhancer(gray).enhance(factor)
    if p.gamma != 1:
        gray = gray.point([round(255 * (i / 255) ** (1 / p.gamma)) for i in range(256)])
    return ImageOps.invert(gray) if p.invert else gray


def distress(values, amount, density, seed):
    if not amount or not density:
        return values
    rng = np.random.default_rng(seed)
    h, w = values.shape
    loss = rng.random((h, w)) < density
    for _ in range(int(density * h * w / 600)):
        y, x = rng.integers(h), rng.integers(w)
        loss[y, x : x + rng.integers(2, 18)] = True
    return values * (1 - loss * (amount / 100))


def ordered(values, size, threshold, amount):
    matrix = np.zeros((1, 1))
    while matrix.shape[0] < size:
        matrix = np.block(
            [[4 * matrix, 4 * matrix + 2], [4 * matrix + 3, 4 * matrix + 1]]
        )
    h, w = values.shape
    offsets = (matrix + 0.5) / size**2 - 0.5
    return (
        values
        >= threshold
        + offsets[np.arange(h)[:, None] % size, np.arange(w) % size] * 255 * amount
    )


def diffusion(values, threshold, amount):
    h, w = values.shape
    # Pixels on x + 2*y = d are independent. Process each diagonal together.
    buf = np.pad(values.astype(np.float64), ((0, 1), (1, 1)))
    mask = np.empty((h, w), dtype=np.uint8)
    for d in range(w + 2 * h - 2):
        y = np.arange(max(0, (d - w + 2) // 2), min(h, d // 2 + 1))
        x = d - 2 * y + 1
        value = buf[y, x]
        bit = value >= threshold
        mask[y, x - 1] = bit
        err = (value - 255.0 * bit) * (amount / 16)
        buf[y, x + 1] += 7 * err
        buf[y + 1, x - 1] += 3 * err
        buf[y + 1, x] += 5 * err
        buf[y + 1, x + 1] += err
    return mask


def process(image: Image.Image, size: tuple[int, int], p: Processing) -> Image.Image:
    if not all(1 <= n <= 4096 for n in size):
        raise ValueError("Output dimensions must be 1–4096")
    gray = luminance(fitted(image, size, p.fit), p)
    return quantize(gray, p)


def quantize(gray: Image.Image, p: Processing) -> Image.Image:
    size = gray.size
    values = np.asarray(gray)
    amount = max(p.grunge, p.strength if p.effect == "grunge" else 0)
    values = distress(values, amount, p.density, p.texture_seed)
    block = 1
    if p.effect == "pixel" and p.strength:
        block = p.block_size or (1 + round(11 * p.strength / 100))
    if block > 1:
        # Extend edge pixels so every full cell is exactly block × block.
        w, h = size
        padded = np.pad(values, ((0, -h % block), (0, -w % block)), mode="edge")
        values = padded.reshape(padded.shape[0] // block, block, -1, block).mean(
            axis=(1, 3)
        )
    threshold = p.threshold
    if p.effect == "threshold":
        threshold = 128 + (threshold - 128) * p.strength / 100
    if p.effect == "floyd" and p.strength:
        bits = diffusion(values, threshold, p.strength / 100)
    elif p.effect == "bayer" and p.strength:
        bits = ordered(values, p.matrix, threshold, p.strength / 100)
    else:
        bits = values >= threshold
    out = Image.fromarray(np.asarray(bits, dtype=np.uint8))
    if block > 1:
        out = out.resize((out.width * block, out.height * block), NEAREST).crop(
            (0, 0, *size)
        )
    out.putpalette([*ImageColor.getrgb(p.background), *ImageColor.getrgb(p.foreground)])
    return out
