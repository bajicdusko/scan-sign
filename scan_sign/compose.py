"""Placement model + stamping overlays into the PDF."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz
from PIL import Image


@dataclass
class Placement:
    """One overlay instance. Geometry is in PDF points, origin = top-left of page.rect."""

    asset: str
    page: int
    cx: float
    cy: float
    width_pt: float
    angle: float = 0.0  # degrees, counter-clockwise


def rendered(asset_image: Image.Image, width_pt: float, angle: float) -> tuple[Image.Image, float, float]:
    """Rotate the asset and return (image, bbox_width_pt, bbox_height_pt)."""
    img = asset_image
    if abs(angle) > 1e-3:
        img = img.rotate(angle, expand=True, resample=Image.BICUBIC)
    scale = width_pt / asset_image.width  # pt per source pixel
    return img, img.width * scale, img.height * scale


def apply_placements(doc: fitz.Document, placements: list[Placement], assets: dict[str, object]) -> None:
    for p in placements:
        asset = assets[p.asset]
        img, w, h = rendered(asset.image, p.width_pt, p.angle)
        page = doc[p.page]
        rect = fitz.Rect(p.cx - w / 2, p.cy - h / 2, p.cx + w / 2, p.cy + h / 2)
        page.insert_image(
            rect,
            stream=asset.png_bytes(img),
            keep_proportion=True,
            overlay=True,
            alpha=1,
        )


def save_layout(path: str | Path, placements: list[Placement]) -> None:
    Path(path).write_text(json.dumps([asdict(p) for p in placements], indent=2))


def load_layout(path: str | Path) -> list[Placement]:
    data = json.loads(Path(path).read_text())
    return [Placement(**item) for item in data]


def auto_placements(doc: fitz.Document, assets: dict[str, object], page_index: int) -> list[Placement]:
    """Fallback layout: signature above the bottom-right, stamp to its left."""
    page = doc[page_index]
    r = page.rect
    out: list[Placement] = []
    if "signature" in assets:
        a = assets["signature"]
        w = a.default_width_pt
        h = w * a.aspect
        out.append(Placement("signature", page_index, r.x1 - 0.18 * r.width - w / 2, r.y1 - 0.12 * r.height - h / 2, w))
    if "stamp" in assets:
        a = assets["stamp"]
        w = a.default_width_pt
        h = w * a.aspect
        out.append(Placement("stamp", page_index, r.x0 + 0.28 * r.width, r.y1 - 0.12 * r.height - h / 2, w, angle=-6))
    return out
