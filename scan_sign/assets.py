"""Loading and cleaning of overlay images (signature, stamp, ...)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# Default on-page width in PDF points (1 pt = 1/72 inch).
DEFAULT_WIDTHS = {
    "signature": 150.0,  # ~5.3 cm
    "stamp": 120.0,  # ~4.2 cm
}


DEFAULT_TOLERANCE = 34  # how far a pixel may stray from the paper colour and still be paper


@dataclass
class Asset:
    name: str
    path: Path
    image: Image.Image  # always RGBA
    default_width_pt: float
    bg_removed: bool = False
    bg_tolerance: int = DEFAULT_TOLERANCE

    @property
    def aspect(self) -> float:
        return self.image.height / self.image.width

    def png_bytes(self, image: Image.Image | None = None) -> bytes:
        buf = io.BytesIO()
        (image or self.image).save(buf, format="PNG")
        return buf.getvalue()


def _paper_field(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel paper colour. A single value can't follow the uneven lighting of a real scan,
    so estimate it locally: shrink hard, take the brightest neighbour (drops the ink), blur back."""
    h, w = rgb.shape[:2]
    cell = max(6, min(h, w) // 24)
    sw, sh = max(2, w // cell), max(2, h // cell)
    small = Image.fromarray(rgb.astype(np.uint8)).resize((sw, sh), Image.BOX)
    small = small.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.0))
    return np.asarray(small.resize((w, h), Image.BILINEAR)).astype(np.float32)


def _drop_background(img: Image.Image, tolerance: int = DEFAULT_TOLERANCE) -> Image.Image:
    """Key out the paper: distance from the local paper colour drives alpha, ink keeps its own."""
    arr = np.asarray(img).astype(np.float32)
    rgb, alpha = arr[..., :3], arr[..., 3:4]
    paper = _paper_field(rgb)

    dist = np.abs(rgb - paper).max(axis=-1, keepdims=True)
    low = tolerance * 0.35  # fully transparent below this
    ramp = np.clip((dist - low) / max(tolerance - low, 1.0), 0.0, 1.0)
    # Snap the last traces of paper to zero — a few percent of alpha across the whole rectangle
    # is exactly what reads as a faint box once the overlay sits on a white page.
    ramp[ramp < 0.14] = 0.0

    # Un-composite: partly transparent pixels are ink mixed with paper, so subtract the paper
    # share. Without this the soft edge keeps a grey/yellow halo from the original scan.
    safe = np.maximum(ramp, 0.06)
    ink = np.clip((rgb - paper * (1.0 - safe)) / safe, 0.0, 255.0)

    out = np.concatenate([np.where(ramp > 0.0, ink, rgb), alpha * ramp], axis=-1)
    return Image.fromarray(out.astype(np.uint8), mode="RGBA")


def _trim(img: Image.Image) -> Image.Image:
    bbox = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return img.crop(bbox) if bbox else img


def _has_alpha(img: Image.Image) -> bool:
    if "A" not in img.getbands():
        return False
    return np.asarray(img.getchannel("A")).min() < 250


def _build(
    raw: Image.Image,
    name: str,
    path: Path,
    *,
    remove_bg: str,
    tolerance: int,
    trim: bool,
    width_pt: float | None,
) -> Asset:
    opaque = not _has_alpha(raw)
    img = raw.convert("RGBA")

    strip = remove_bg == "always" or (remove_bg == "auto" and opaque)
    if strip:
        img = _drop_background(img, tolerance=tolerance)
    if trim:
        img = _trim(img)

    return Asset(
        name=name,
        path=path,
        image=img,
        default_width_pt=width_pt or DEFAULT_WIDTHS.get(name, 140.0),
        bg_removed=strip,
        bg_tolerance=tolerance,
    )


def load_asset(
    path: str | Path,
    name: str,
    *,
    remove_bg: str = "auto",  # "auto" | "always" | "never"
    tolerance: int = DEFAULT_TOLERANCE,
    trim: bool = True,
    width_pt: float | None = None,
) -> Asset:
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{name} image not found: {path}")
    return _build(
        Image.open(path), name, path, remove_bg=remove_bg, tolerance=tolerance, trim=trim, width_pt=width_pt
    )


def load_asset_bytes(
    data: bytes,
    name: str,
    *,
    filename: str = "",
    remove_bg: str = "auto",
    tolerance: int = DEFAULT_TOLERANCE,
    trim: bool = True,
    width_pt: float | None = None,
) -> Asset:
    """Same as load_asset for an in-memory upload."""
    return _build(
        Image.open(io.BytesIO(data)),
        name,
        Path(filename or f"{name}.png"),
        remove_bg=remove_bg,
        tolerance=tolerance,
        trim=trim,
        width_pt=width_pt,
    )
