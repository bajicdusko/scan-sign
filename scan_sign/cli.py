"""Console entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz

from .assets import DEFAULT_TOLERANCE, load_asset
from .compose import apply_placements, auto_placements, load_layout, save_layout
from .scanify import scanify_document


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scan-sign",
        description="Place a signature and/or stamp onto a PDF, optionally faking a scanned look.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  scan-sign                                       # browser app: pick files, download result\n"
            "  scan-sign contract.pdf -s sig.png -t stamp.png\n"
            "  scan-sign contract.pdf -s sig.png --scan --scan-strength 0.7\n"
            "  scan-sign contract.pdf -s sig.png --layout last.json --no-gui\n"
        ),
    )
    p.add_argument("pdf", nargs="?", help="input PDF (omit to load it from the browser instead)")
    p.add_argument("-s", "--signature", help="signature image (PNG with or without alpha)")
    p.add_argument("-t", "--stamp", help="stamp image")
    p.add_argument("-o", "--output", help="output PDF (default: <input>-signed.pdf)")
    p.add_argument("-p", "--page", type=int, default=0, help="page to open / auto-place on, 1-based (default: last)")

    g = p.add_argument_group("placement")
    g.add_argument("--no-gui", action="store_true", help="skip the picker; use --layout or automatic placement")
    g.add_argument(
        "--picker",
        choices=["web", "tk"],
        default="web",
        help="placement UI: web = local browser page (default), tk = native Tkinter window",
    )
    g.add_argument("--no-browser", action="store_true", help="don't auto-open the browser; just print the URL")
    g.add_argument("--port", type=int, default=0, help="port for the browser UI (default: a free one)")
    g.add_argument("--layout", help="JSON layout file to start from (or use as-is with --no-gui)")
    g.add_argument("--save-layout", help="write the resulting placements to this JSON file")
    g.add_argument("--signature-width", type=float, help="default signature width in mm")
    g.add_argument("--stamp-width", type=float, help="default stamp width in mm")
    g.add_argument(
        "--remove-bg",
        choices=["auto", "always", "never"],
        default="auto",
        help="drop the white background of overlay images (default: auto = only when the image has no alpha)",
    )
    g.add_argument(
        "--bg-tolerance",
        type=int,
        default=DEFAULT_TOLERANCE,
        help=f"how far a pixel may differ from the paper colour and still count as background "
        f"(0-120, default {DEFAULT_TOLERANCE}); raise it for grey or yellowed scans",
    )

    sc = p.add_argument_group("scan simulation")
    sc.add_argument("--scan", action="store_true", help="rasterize and add scanner artifacts")
    sc.add_argument("--scan-strength", type=float, default=0.5, help="0..1 amount of noise/blur/contrast (default 0.5)")
    sc.add_argument("--scan-dpi", type=int, default=200, help="rasterization DPI (default 200)")
    sc.add_argument("--rotate", type=float, default=0.8, help="max random page skew in degrees (default 0.8)")
    sc.add_argument("--grayscale", action="store_true", help="scan in grayscale")
    sc.add_argument("--keep-edges", action="store_true", help="show the paper edge on a scanner-lid background")
    sc.add_argument("--no-corner-damage", action="store_true", help="skip the dark corner smudge")
    sc.add_argument("--seed", type=int, help="reproducible scan artifacts")
    return p


def _serve_only(args) -> int:
    """No PDF on the command line: run the browser app, files come from the file picker."""
    from .webpicker import Session, serve

    session = Session(mode="serve")
    mm = 72.0 / 25.4
    for name, path, width in (
        ("signature", args.signature, args.signature_width),
        ("stamp", args.stamp, args.stamp_width),
    ):
        if path:
            p = Path(path).expanduser()
            asset = load_asset(
                p, name, remove_bg=args.remove_bg, tolerance=args.bg_tolerance,
                width_pt=width * mm if width else None,
            )
            session.raw_assets[name] = p.read_bytes()
            session.bg_mode[name] = args.remove_bg
            session.assets[name] = asset
    serve(session, open_browser=not args.no_browser, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.pdf:
        if args.no_gui:
            print("error: --no-gui needs a PDF argument", file=sys.stderr)
            return 2
        return _serve_only(args)

    src = Path(args.pdf).expanduser()
    if not src.is_file():
        print(f"error: PDF not found: {src}", file=sys.stderr)
        return 2
    if not (args.signature or args.stamp or args.layout):
        print("error: give at least --signature, --stamp or --layout", file=sys.stderr)
        return 2

    doc = fitz.open(src)
    page_index = doc.page_count - 1 if args.page == 0 else max(0, min(args.page - 1, doc.page_count - 1))

    mm = 72.0 / 25.4
    assets = {}
    if args.signature:
        assets["signature"] = load_asset(
            args.signature,
            "signature",
            remove_bg=args.remove_bg,
            tolerance=args.bg_tolerance,
            width_pt=args.signature_width * mm if args.signature_width else None,
        )
    if args.stamp:
        assets["stamp"] = load_asset(
            args.stamp,
            "stamp",
            remove_bg=args.remove_bg,
            tolerance=args.bg_tolerance,
            width_pt=args.stamp_width * mm if args.stamp_width else None,
        )

    placements = load_layout(args.layout) if args.layout else []
    missing = {p.asset for p in placements} - set(assets)
    if missing:
        print(f"error: layout references assets not provided: {', '.join(sorted(missing))}", file=sys.stderr)
        return 2

    if args.no_gui:
        if not placements:
            placements = auto_placements(doc, assets, page_index)
    else:
        if not assets:
            print("error: the picker needs --signature and/or --stamp", file=sys.stderr)
            return 2
        if args.picker == "tk":
            from .placement import pick_placements

            picked = pick_placements(doc, assets, placements, page_index)
        else:
            from .webpicker import pick_placements

            out_preview = Path(args.output).expanduser() if args.output else src.with_name(f"{src.stem}-signed.pdf")
            picked = pick_placements(
                doc,
                assets,
                placements,
                page_index,
                open_browser=not args.no_browser,
                port=args.port,
                pdf_name=src.name,
                output=out_preview,
                bg_mode=args.remove_bg,
            )
        if picked is None:
            print("closed without saving — nothing written")
            return 1
        placements = picked

    if not placements:
        print("warning: no placements — writing the document unchanged", file=sys.stderr)

    apply_placements(doc, placements, assets)

    if args.save_layout:
        save_layout(args.save_layout, placements)
        print(f"layout  → {args.save_layout}")

    if args.scan:
        scanned = scanify_document(
            doc,
            dpi=args.scan_dpi,
            strength=args.scan_strength,
            max_angle=args.rotate,
            grayscale=args.grayscale,
            keep_edges=args.keep_edges,
            corner_damage=not args.no_corner_damage,
            seed=args.seed,
        )
        doc.close()
        doc = scanned

    out = Path(args.output).expanduser() if args.output else src.with_name(f"{src.stem}-signed.pdf")
    doc.save(str(out), garbage=3, deflate=True)
    doc.close()
    print(f"written → {out}")
    return 0
