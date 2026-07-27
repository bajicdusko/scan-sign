"""Interactive placement window (Tkinter): click to drop, drag to move, wheel to size.

Optional alternative to the browser picker in ``scan_sign.webpicker``; enable with ``--picker tk``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import fitz
from PIL import Image, ImageTk

from .compose import Placement, rendered

HELP = (
    "click page = place  ·  drag = move  ·  wheel/+- = size  ·  [ ] = rotate  "
    "·  arrows = nudge  ·  del = remove  ·  Enter = save  ·  Esc = cancel"
)


class PlacementApp:
    def __init__(self, doc: fitz.Document, assets: dict, initial: list[Placement] | None = None, page: int = 0):
        self.doc = doc
        self.assets = assets
        self.placements: list[Placement] = list(initial or [])
        self.page_index = max(0, min(page, doc.page_count - 1))
        self.selected: Placement | None = None
        self.result: list[Placement] | None = None
        self._drag: tuple[float, float] | None = None
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._page_photo = None

        self.root = tk.Tk()
        self.root.title("scan-sign · place signature & stamp")
        self._build_ui()
        self._fit_page()
        self._render_page()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = self.root
        side = ttk.Frame(root, padding=8)
        side.grid(row=0, column=0, sticky="ns")

        ttk.Label(side, text="Assets", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.asset_var = tk.StringVar(value=next(iter(self.assets)))
        for name in self.assets:
            ttk.Radiobutton(side, text=name, value=name, variable=self.asset_var).pack(anchor="w")

        ttk.Separator(side, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(side, text="Page").pack(anchor="w")
        nav = ttk.Frame(side)
        nav.pack(anchor="w", pady=2)
        ttk.Button(nav, text="◀", width=3, command=lambda: self._change_page(-1)).pack(side="left")
        self.page_label = ttk.Label(nav, text="")
        self.page_label.pack(side="left", padx=6)
        ttk.Button(nav, text="▶", width=3, command=lambda: self._change_page(1)).pack(side="left")

        ttk.Separator(side, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(side, text="Selected").pack(anchor="w")
        self.info = ttk.Label(side, text="—", justify="left")
        self.info.pack(anchor="w", pady=2)
        ttk.Button(side, text="Delete", command=self._delete).pack(fill="x", pady=2)
        ttk.Button(side, text="Duplicate", command=self._duplicate).pack(fill="x", pady=2)
        ttk.Button(side, text="Reset angle", command=self._reset_angle).pack(fill="x", pady=2)

        ttk.Separator(side, orient="horizontal").pack(fill="x", pady=8)
        ttk.Button(side, text="Save & close", command=self._done).pack(fill="x", pady=2)
        ttk.Button(side, text="Cancel", command=self._cancel).pack(fill="x", pady=2)

        self.canvas = tk.Canvas(root, background="#3a3a3a", highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=0, column=1, sticky="nsew")
        ttk.Label(root, text=HELP, padding=4).grid(row=1, column=0, columnspan=2, sticky="w")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        c = self.canvas
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", lambda e: self._scale_selected(1.03))
        c.bind("<Button-5>", lambda e: self._scale_selected(1 / 1.03))
        root.bind("<Key>", self._on_key)
        root.protocol("WM_DELETE_WINDOW", self._cancel)

    # -------------------------------------------------------------- helpers
    def _fit_page(self) -> None:
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth() * 0.72
        sh = self.root.winfo_screenheight() * 0.82
        r = self.doc[self.page_index].rect
        self.zoom = max(0.2, min(sw / r.width, sh / r.height))
        self.origin = (r.x0, r.y0)
        self.canvas.config(width=int(r.width * self.zoom), height=int(r.height * self.zoom))

    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.origin[0]) * self.zoom, (y - self.origin[1]) * self.zoom

    def _to_pdf(self, sx: float, sy: float) -> tuple[float, float]:
        return sx / self.zoom + self.origin[0], sy / self.zoom + self.origin[1]

    def _current(self) -> list[Placement]:
        return [p for p in self.placements if p.page == self.page_index]

    def _bbox(self, p: Placement) -> tuple[float, float, float, float]:
        _, w, h = rendered(self.assets[p.asset].image, p.width_pt, p.angle)
        cx, cy = self._to_screen(p.cx, p.cy)
        w, h = w * self.zoom, h * self.zoom
        return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2

    # ------------------------------------------------------------ rendering
    def _render_page(self) -> None:
        page = self.doc[self.page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        self._page_photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._page_photo, anchor="nw")
        self.page_label.config(text=f"{self.page_index + 1} / {self.doc.page_count}")
        self._render_overlays()

    def _render_overlays(self) -> None:
        self.canvas.delete("ov")
        self._photos.clear()
        for p in self._current():
            asset = self.assets[p.asset]
            img, w, h = rendered(asset.image, p.width_pt, p.angle)
            tw, th = max(1, int(w * self.zoom)), max(1, int(h * self.zoom))
            photo = ImageTk.PhotoImage(img.resize((tw, th), Image.LANCZOS))
            self._photos[id(p)] = photo
            sx, sy = self._to_screen(p.cx, p.cy)
            self.canvas.create_image(sx, sy, image=photo, anchor="center", tags="ov")
            if p is self.selected:
                x0, y0, x1, y1 = self._bbox(p)
                self.canvas.create_rectangle(x0, y0, x1, y1, outline="#1e90ff", dash=(4, 3), width=2, tags="ov")
        self._update_info()

    def _update_info(self) -> None:
        p = self.selected
        if not p:
            self.info.config(text="—")
            return
        h = p.width_pt * self.assets[p.asset].aspect
        self.info.config(
            text=f"{p.asset}\n{p.width_pt:.0f} × {h:.0f} pt\n"
            f"({p.width_pt / 72 * 25.4:.0f} × {h / 72 * 25.4:.0f} mm)\nangle {p.angle:+.1f}°"
        )

    # -------------------------------------------------------------- actions
    def _on_press(self, event) -> None:
        for p in reversed(self._current()):
            x0, y0, x1, y1 = self._bbox(p)
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.selected = p
                self._drag = (event.x - (x0 + x1) / 2, event.y - (y0 + y1) / 2)
                self._render_overlays()
                return
        name = self.asset_var.get()
        asset = self.assets[name]
        cx, cy = self._to_pdf(event.x, event.y)
        p = Placement(name, self.page_index, cx, cy, asset.default_width_pt)
        self.placements.append(p)
        self.selected = p
        self._drag = (0.0, 0.0)
        self._render_overlays()

    def _on_drag(self, event) -> None:
        if not (self.selected and self._drag):
            return
        dx, dy = self._drag
        self.selected.cx, self.selected.cy = self._to_pdf(event.x - dx, event.y - dy)
        self._render_overlays()

    def _on_wheel(self, event) -> None:
        step = 1.0 + max(-0.2, min(0.2, event.delta / 120 * 0.06 if abs(event.delta) >= 120 else event.delta * 0.01))
        self._scale_selected(step)

    def _scale_selected(self, factor: float) -> None:
        if not self.selected:
            return
        self.selected.width_pt = max(8.0, min(2000.0, self.selected.width_pt * factor))
        self._render_overlays()

    def _rotate_selected(self, delta: float) -> None:
        if not self.selected:
            return
        self.selected.angle = (self.selected.angle + delta + 180) % 360 - 180
        self._render_overlays()

    def _nudge(self, dx: float, dy: float) -> None:
        if not self.selected:
            return
        self.selected.cx += dx
        self.selected.cy += dy
        self._render_overlays()

    def _delete(self) -> None:
        if self.selected in self.placements:
            self.placements.remove(self.selected)
            self.selected = None
            self._render_overlays()

    def _duplicate(self) -> None:
        if not self.selected:
            return
        p = self.selected
        clone = Placement(p.asset, p.page, p.cx + 20, p.cy + 20, p.width_pt, p.angle)
        self.placements.append(clone)
        self.selected = clone
        self._render_overlays()

    def _reset_angle(self) -> None:
        if self.selected:
            self.selected.angle = 0.0
            self._render_overlays()

    def _change_page(self, delta: int) -> None:
        new = self.page_index + delta
        if 0 <= new < self.doc.page_count:
            self.page_index = new
            self.selected = None
            self._fit_page()
            self._render_page()

    def _on_key(self, event) -> None:
        key, shift = event.keysym, bool(event.state & 0x0001)
        step = 10.0 if shift else 1.0
        actions = {
            "Left": lambda: self._nudge(-step, 0),
            "Right": lambda: self._nudge(step, 0),
            "Up": lambda: self._nudge(0, -step),
            "Down": lambda: self._nudge(0, step),
            "bracketleft": lambda: self._rotate_selected(5 if shift else 1),
            "bracketright": lambda: self._rotate_selected(-5 if shift else -1),
            "plus": lambda: self._scale_selected(1.05),
            "equal": lambda: self._scale_selected(1.05),
            "minus": lambda: self._scale_selected(1 / 1.05),
            "BackSpace": self._delete,
            "Delete": self._delete,
            "Return": self._done,
            "KP_Enter": self._done,
            "Escape": self._cancel,
            "Next": lambda: self._change_page(1),
            "Prior": lambda: self._change_page(-1),
            "Tab": self._cycle_asset,
        }
        if key in actions:
            actions[key]()

    def _cycle_asset(self) -> None:
        names = list(self.assets)
        self.asset_var.set(names[(names.index(self.asset_var.get()) + 1) % len(names)])

    def _done(self) -> None:
        self.result = self.placements
        self.root.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.root.destroy()

    def run(self) -> list[Placement] | None:
        self.root.mainloop()
        return self.result


def pick_placements(doc, assets, initial=None, page: int = 0):
    return PlacementApp(doc, assets, initial, page).run()
