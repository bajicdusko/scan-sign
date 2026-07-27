"""Make a clean PDF look like it went through a mediocre office scanner."""

from __future__ import annotations

import io
import random

import fitz
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def _rotate_slightly(img: Image.Image, angle: float, keep_edges: bool) -> Image.Image:
    """Rotate the page. keep_edges=True shows the paper border on a scanner-lid background."""
    w, h = img.size
    if keep_edges:
        pad = int(max(w, h) * 0.02)
        bg = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (232, 231, 228))
        bg.paste(img, (pad, pad))
        out = bg.rotate(angle, resample=Image.BICUBIC, fillcolor=(232, 231, 228))
        return out.resize((w, h), Image.LANCZOS)
    # Over-scale slightly so the rotation does not expose empty corners.
    over = 1.0 + abs(angle) * 0.02 + 0.01
    big = img.resize((int(w * over), int(h * over)), Image.LANCZOS)
    big = big.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    left, top = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((left, top, left + w, top + h))


def _radial_mask(size: tuple[int, int], cx: float, cy: float, radius: float) -> np.ndarray:
    w, h = size
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xs - cx * w) / (radius * w)) ** 2 + ((ys - cy * h) / (radius * h)) ** 2)
    return np.clip(1.0 - d, 0.0, 1.0) ** 2


def _corner_shadow(arr: np.ndarray, rng: random.Random, strength: float) -> np.ndarray:
    """Dark smudge / lifted-corner shadow near a random corner, plus a soft edge gradient."""
    h, w = arr.shape[:2]
    corner = rng.choice([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])
    radius = rng.uniform(0.22, 0.42)
    mask = _radial_mask((w, h), corner[0], corner[1], radius)
    darkening = 1.0 - mask * (0.06 + 0.16 * strength) * rng.uniform(0.6, 1.0)

    # A second, tighter blob offset a bit — reads as a fold/crease.
    ox = corner[0] + (0.08 if corner[0] == 0.0 else -0.08)
    oy = corner[1] + (0.06 if corner[1] == 0.0 else -0.06)
    darkening *= 1.0 - _radial_mask((w, h), ox, oy, radius * 0.45) * 0.08 * strength

    return arr * darkening[..., None]


def _vignette(arr: np.ndarray, strength: float) -> np.ndarray:
    h, w = arr.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xs / w - 0.5) * 2
    dy = (ys / h - 0.5) * 2
    d = np.sqrt(dx**2 + dy**2) / np.sqrt(2)
    return arr * (1.0 - (d**2) * 0.06 * strength)[..., None]


def scanify_image(
    img: Image.Image,
    *,
    rng: random.Random,
    strength: float = 0.5,
    angle: float | None = None,
    max_angle: float = 0.8,
    grayscale: bool = False,
    keep_edges: bool = False,
    corner_damage: bool = True,
) -> Image.Image:
    s = max(0.0, min(1.0, strength))
    if angle is None:
        angle = rng.uniform(-max_angle, max_angle)
        if abs(angle) < max_angle * 0.25:  # avoid an uncannily straight result
            angle = max_angle * 0.3 * (1 if angle >= 0 else -1)

    img = img.convert("RGB")
    img = _rotate_slightly(img, angle, keep_edges)

    if grayscale:
        img = img.convert("L").convert("RGB")

    img = img.filter(ImageFilter.GaussianBlur(radius=0.15 + 0.45 * s))
    img = ImageEnhance.Contrast(img).enhance(1.0 + 0.10 * s * rng.uniform(0.6, 1.4))
    img = ImageEnhance.Brightness(img).enhance(1.0 + rng.uniform(-0.02, 0.02) * s)

    arr = np.asarray(img).astype(np.float32)

    # Barely-there paper tint — a current scanner is close to neutral.
    if not grayscale:
        tint = np.array([1.0, 0.999, 0.993], dtype=np.float32)
        arr *= 1.0 + (tint - 1.0) * s * rng.uniform(0.7, 1.3)

    # Sensor noise + paper grain, both kept low: modern CIS sensors are quiet.
    noise = np.random.default_rng(rng.randrange(2**32)).normal(0, 0.5 + 2.0 * s, arr.shape[:2])
    arr += noise[..., None]

    grain = np.random.default_rng(rng.randrange(2**32)).normal(0, 1.0, (arr.shape[0] // 4 + 1, arr.shape[1] // 4 + 1))
    grain = np.asarray(Image.fromarray(grain.astype(np.float32)).resize(img.size, Image.BILINEAR))
    arr += grain[..., None] * (0.5 + 1.8 * s)

    # Horizontal scanner streaks.
    streaks = np.random.default_rng(rng.randrange(2**32)).normal(0, 1.0, arr.shape[0])
    arr += streaks[:, None, None] * 0.4 * s

    # Auto-levels: scanners lift the white point, so the paper reads white rather than dingy.
    white = np.percentile(arr, 99.0)
    if white > 1.0:
        arr *= min(255.0 / white, 1.06)

    if corner_damage:
        arr = _corner_shadow(arr, rng, s)
    arr = _vignette(arr, s)

    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")

    # Recompression artifacts.
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=int(96 - 16 * s))
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def scanify_document(
    doc: fitz.Document,
    *,
    dpi: int = 200,
    strength: float = 0.5,
    max_angle: float = 0.8,
    grayscale: bool = False,
    keep_edges: bool = False,
    corner_damage: bool = True,
    seed: int | None = None,
    jpeg_quality: int = 90,
) -> fitz.Document:
    rng = random.Random(seed)
    out = fitz.open()
    zoom = dpi / 72.0
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = scanify_image(
            img,
            rng=rng,
            strength=strength,
            max_angle=max_angle,
            grayscale=grayscale,
            keep_edges=keep_edges,
            corner_damage=corner_damage,
        )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buf.getvalue())
    return out
