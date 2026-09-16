"""Browser UI: load files, place the overlays, download (or hand back to the CLI)."""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import unicodedata
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import fitz
from PIL import Image

from ..assets import DEFAULT_TOLERANCE, DEFAULT_WIDTHS, load_asset_bytes
from ..compose import Placement, apply_placements
from ..scanify import scanify_document
from .memory import release_free_memory
from .sessions import CookieSessions, SingleSession

HERE = Path(__file__).parent
PREVIEW_DPI = 110
ASSET_SLOTS = ("signature", "stamp")
COOKIE = "ss_sid"

# Hosted guard rails. A public instance renders whatever it is handed, so cap the work per
# request: an unbounded PDF at an unbounded DPI is a trivial way to exhaust a small container.
MAX_UPLOAD_BYTES = int(os.environ.get("SCAN_SIGN_MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_PAGES = int(os.environ.get("SCAN_SIGN_MAX_PAGES", "40"))
MAX_SCAN_DPI = 200

# The one ask in the whole tool: a coffee, offered next to the finished download.
# Set SCAN_SIGN_SUPPORT_URL="" to drop the prompt entirely.
SUPPORT_URL = os.environ.get("SCAN_SIGN_SUPPORT_URL", "https://buymeacoffee.com/bajicdusko").strip()

BMC_HOSTS = ("buymeacoffee.com", "buymeacoff.ee")


def _content_disposition(filename: str) -> str:
    """An RFC 6266 attachment header that survives ``send_header``'s strict latin-1 encoding.

    The plain ``filename=`` gets an ASCII fallback (NFKD-stripped, quotes/backslashes/controls
    removed); the real name rides along as ``filename*=UTF-8''...`` only when it differs.
    """
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r'[\x00-\x1f\x7f"\\]', "", ascii_name).strip(" .")
    if ascii_name.lower() in ("", ".pdf", "pdf"):
        ascii_name = "document-signed.pdf"
    elif not ascii_name.lower().endswith(".pdf"):
        ascii_name += ".pdf"
    value = f'attachment; filename="{ascii_name}"'
    if ascii_name != filename:
        value += f"; filename*=UTF-8''{quote(filename, safe='')}"
    return value


def bmc_slug(url: str) -> str | None:
    """The account name in a Buy Me a Coffee link, or None if the link isn't one of theirs.

    Their button widget can only point at a slug on their own domain, so anyone who
    redirects SCAN_SIGN_SUPPORT_URL somewhere else gets the plain link instead — the
    tip must never land in an account the operator didn't choose.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    if parts.netloc.lower().removeprefix("www.") not in BMC_HOSTS:
        return None
    slug = parts.path.strip("/").split("/")[0]
    return slug if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", slug) else None


class Session:
    """Everything the browser can look at or replace, guarded by one lock."""

    def __init__(self, mode: str, output: Path | None = None, name: str = ""):
        self.mode = mode
        self.output = output
        self.name = name
        self.lock = threading.RLock()
        self.doc: fitz.Document | None = None
        self.pdf_name: str | None = None
        self.pdf_bytes: bytes | None = None
        self.assets: dict = {}
        self.raw_assets: dict[str, bytes] = {}
        self.bg_mode: dict[str, str] = {}
        self.bg_tolerance: dict[str, int] = {}
        self.placements: list[Placement] = []
        self.initial_page = 0
        self.token = 0
        self._page_png: dict[int, bytes] = {}
        self._asset_png: dict[str, bytes] = {}
        self.result: list[Placement] | None = None
        self.done = threading.Event()

    # ------------------------------------------------------------- mutation
    def set_pdf(self, data: bytes, name: str) -> None:
        with self.lock:
            doc = fitz.open(stream=data, filetype="pdf")
            if doc.page_count > MAX_PAGES:
                doc.close()
                raise ValueError(f"too many pages ({doc.page_count}); this instance allows {MAX_PAGES}")
            if self.doc is not None:
                self.doc.close()
            self.doc, self.pdf_bytes, self.pdf_name = doc, data, name
            self.placements = []
            self.initial_page = doc.page_count - 1
            self._page_png.clear()
            self.token += 1

    def set_asset(self, name: str, data: bytes, filename: str, mode: str | None = None) -> None:
        with self.lock:
            self.raw_assets[name] = data
            self.bg_mode[name] = mode or self.bg_mode.get(name, "auto")
            self._reload_asset(name, filename)

    def set_bg(self, name: str, mode: str | None = None, tolerance: int | None = None) -> None:
        with self.lock:
            if name not in self.raw_assets:
                return
            if mode:
                self.bg_mode[name] = mode
            if tolerance is not None:
                self.bg_tolerance[name] = max(0, min(120, int(tolerance)))
            self._reload_asset(name, self.assets[name].path.name)

    def _reload_asset(self, name: str, filename: str) -> None:
        keep_width = self.assets[name].default_width_pt if name in self.assets else DEFAULT_WIDTHS.get(name)
        self.assets[name] = load_asset_bytes(
            self.raw_assets[name],
            name,
            filename=filename,
            remove_bg=self.bg_mode[name],
            tolerance=self.bg_tolerance.get(name, DEFAULT_TOLERANCE),
            width_pt=keep_width,
        )
        self._asset_png.pop(name, None)
        self.token += 1

    # -------------------------------------------------------------- reading
    def page_png(self, i: int) -> bytes:
        with self.lock:
            if i not in self._page_png:
                zoom = PREVIEW_DPI / 72.0
                pix = self.doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                self._page_png[i] = buf.getvalue()
            return self._page_png[i]

    def asset_png(self, name: str) -> bytes:
        with self.lock:
            if name not in self._asset_png:
                self._asset_png[name] = self.assets[name].png_bytes()
            return self._asset_png[name]

    def manifest(self) -> dict:
        with self.lock:
            return {
                "mode": self.mode,
                "name": self.name,
                "token": self.token,
                "support_url": SUPPORT_URL or None,
                "output": str(self.output) if self.output else None,
                "pdf": (
                    None
                    if self.doc is None
                    else {
                        "name": self.pdf_name,
                        "pages": [
                            {"index": i, "width": p.rect.width, "height": p.rect.height,
                             "url": f"/page/{i}.png?v={self.token}"}
                            for i, p in enumerate(self.doc)
                        ],
                    }
                ),
                "slots": [
                    {
                        "name": n,
                        "loaded": n in self.assets,
                        "filename": self.assets[n].path.name if n in self.assets else None,
                        "url": f"/asset/{n}.png?v={self.token}" if n in self.assets else None,
                        "aspect": self.assets[n].aspect if n in self.assets else 1.0,
                        "default_width_pt": self.assets[n].default_width_pt if n in self.assets
                        else DEFAULT_WIDTHS.get(n, 140.0),
                        "bg_mode": self.bg_mode.get(n, "auto"),
                        "bg_removed": getattr(self.assets.get(n), "bg_removed", False),
                        "bg_tolerance": self.bg_tolerance.get(n, DEFAULT_TOLERANCE),
                    }
                    for n in ASSET_SLOTS
                ],
                "placements": [p.__dict__ for p in self.placements],
                "initial_page": self.initial_page,
            }

    def close(self) -> None:
        with self.lock:
            if self.doc is not None:
                self.doc.close()
                self.doc = None
            self.pdf_bytes = None
            self.raw_assets.clear()
            self.assets.clear()
            self._page_png.clear()
            self._asset_png.clear()

    # ------------------------------------------------------------- producing
    def build_pdf(self, placements: list[Placement], scan: dict | None) -> tuple[bytes, str]:
        with self.lock:
            if self.doc is None:
                raise ValueError("no PDF loaded")
            doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
            try:
                apply_placements(doc, placements, self.assets)
                if scan and scan.get("enabled"):
                    scanned = scanify_document(
                        doc,
                        dpi=max(72, min(MAX_SCAN_DPI, int(scan.get("dpi", 200)))),
                        strength=float(scan.get("strength", 0.5)),
                        max_angle=float(scan.get("rotate", 0.8)),
                        grayscale=bool(scan.get("grayscale")),
                        keep_edges=bool(scan.get("keep_edges")),
                        corner_damage=bool(scan.get("corner_damage", True)),
                        seed=scan.get("seed"),
                    )
                    doc.close()
                    doc = scanned
                data = doc.tobytes(garbage=3, deflate=True)
            finally:
                doc.close()
            stem = Path(self.pdf_name or "document.pdf").stem
            result = (data, f"{stem}-signed.pdf")

        # Outside the lock: the rasters this render just dropped are the biggest allocation the
        # process makes, and reclaiming them should not hold up the next request.
        release_free_memory()
        return result


def _parse_placements(items) -> list[Placement]:
    return [
        Placement(
            asset=i["asset"],
            page=int(i["page"]),
            cx=float(i["cx"]),
            cy=float(i["cy"]),
            width_pt=float(i["width_pt"]),
            angle=float(i.get("angle", 0.0)),
        )
        for i in items
    ]


def _handler(store):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        session = None
        _new_cookie = None

        def log_message(self, *args):
            pass

        def _send(self, body: bytes, ctype: str, status: int = 200, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if self._new_cookie:
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                self.send_header(
                    "Set-Cookie", f"{COOKIE}={self._new_cookie}; Path=/; HttpOnly; SameSite=Lax{secure}"
                )
                self._new_cookie = None
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(json.dumps(obj).encode(), "application/json", status)

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_UPLOAD_BYTES:
                raise ValueError(f"upload too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
            return self.rfile.read(length) if length else b""

        def _landing(self) -> bytes:
            html = (HERE / "landing.html").read_bytes()
            # No ask means no ask: the block goes, and with it the call to their CDN.
            if not SUPPORT_URL:
                html = re.sub(rb"<!--support-->.*?<!--/support-->", b"", html, flags=re.S)
                return html.replace(b"__BMC_STATE__", b"")
            slug = bmc_slug(SUPPORT_URL)
            if slug:
                html = html.replace(b"__BMC_SLUG__", slug.encode())
                html = html.replace(b"__BMC_STATE__", b"has-widget")
            else:
                html = re.sub(rb"<!--bmc-->.*?<!--/bmc-->", b"", html, flags=re.S)
                html = html.replace(b"__BMC_STATE__", b"")
            return html.replace(b"__SUPPORT_URL__", SUPPORT_URL.encode())

        def _cookie(self) -> str | None:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            return jar[COOKIE].value if COOKIE in jar else None

        def _begin(self) -> bool:
            self.session, self._new_cookie = store.resolve(self._cookie())
            return True

        def _no_session(self) -> None:
            self._json({"mode": "hosted", "needs_session": True}, 409)

        def do_GET(self):
            if not self._begin():
                return
            s, path = self.session, self.path.split("?")[0]
            try:
                # Locally the tool *is* the app, so "/" opens it. A public instance gets the
                # landing page as its front door and the app one click away at /app.
                if path in ("/", "/index.html"):
                    path = "/landing" if store.hosted else "/app"
                if path == "/app":
                    return self._send((HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/landing":
                    return self._send(self._landing(), "text/html; charset=utf-8")
                # Which claim the landing page may make about privacy depends on the mode, and
                # it has no session to ask.
                if path == "/api/mode":
                    return self._json({"mode": "hosted" if store.hosted else "local"})
                if s is None:
                    return self._no_session()

                if path == "/api/doc":
                    self._json(s.manifest())
                elif path.startswith("/page/"):
                    self._send(s.page_png(int(path[len("/page/") : -len(".png")])), "image/png")
                elif path.startswith("/asset/"):
                    self._send(s.asset_png(path[len("/asset/") : -len(".png")]), "image/png")
                else:
                    self._send(b"not found", "text/plain", 404)
            except (KeyError, ValueError, IndexError, TypeError) as e:
                self._json({"error": str(e)}, 400)

        def do_POST(self):
            if not self._begin():
                return
            s, path = self.session, self.path.split("?")[0]
            try:
                # Starting is the one thing you can do without a session, and it always makes a
                # new one — a returning visitor never lands back inside the previous session.
                if path == "/api/start":
                    self.session, self._new_cookie = store.start(self._cookie())
                    return self._json(self.session.manifest())

                if path == "/api/end":
                    store.end(self._cookie())
                    return self._json({"ok": True})

                if s is None:
                    return self._no_session()

                if path.startswith("/api/upload/"):
                    kind = path[len("/api/upload/") :]
                    filename = unquote(self.headers.get("X-Filename", kind))
                    data = self._body()
                    if kind == "pdf":
                        s.set_pdf(data, filename)
                    elif kind in ASSET_SLOTS:
                        s.set_asset(kind, data, filename)
                    else:
                        return self._json({"error": f"unknown upload kind {kind}"}, 400)
                    return self._json(s.manifest())

                if path.startswith("/api/bg/"):
                    payload = json.loads(self._body() or b"{}")
                    s.set_bg(path[len("/api/bg/") :], payload.get("mode"), payload.get("tolerance"))
                    return self._json(s.manifest())

                if path == "/api/render":
                    payload = json.loads(self._body() or b"{}")
                    data, filename = s.build_pdf(_parse_placements(payload.get("placements", [])), payload.get("scan"))
                    return self._send(
                        data,
                        "application/pdf",
                        extra={"Content-Disposition": _content_disposition(filename)},
                    )

                # Handing results back to a terminal, and stopping the process, only make sense
                # for the local tool — a visitor must not be able to end everyone's session.
                if not store.hosted:
                    if path == "/api/save":
                        s.result = _parse_placements(json.loads(self._body() or b"[]"))
                        self._json({"ok": True})
                        return s.done.set()

                    if path in ("/api/cancel", "/api/quit"):
                        s.result = None
                        self._json({"ok": True})
                        return s.done.set()

                self._send(b"not found", "text/plain", 404)
            except Exception as e:  # surface the reason in the UI instead of a bare 500
                self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    return Handler


def serve(session: Session, open_browser: bool = True, port: int = 0):
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(SingleSession(session)))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"scan-sign UI → {url}   (Ctrl-C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        session.done.wait()
    except KeyboardInterrupt:
        session.result = None
        print()
    finally:
        threading.Thread(target=server.shutdown, daemon=True).start()
    return session.result


def serve_hosted(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Public deployment: visitors name a session, and sessions are never resumed."""
    store = CookieSessions(factory=lambda: Session(mode="hosted"))
    server = ThreadingHTTPServer((host, port), _handler(store))
    server.daemon_threads = True

    def janitor():
        while True:
            time.sleep(60)
            store.sweep()

    threading.Thread(target=janitor, daemon=True).start()
    print(
        f"scan-sign listening on {host}:{port} "
        f"(sessions={store.max_sessions}, idle timeout={store.ttl}s, "
        f"upload limit={MAX_UPLOAD_BYTES // (1024 * 1024)}MB)",
        flush=True,
    )
    server.serve_forever()


def pick_placements(doc, assets, initial=None, page: int = 0, open_browser: bool = True, **kw):
    """CLI path: the document and assets are already loaded, return the chosen placements."""
    s = Session(mode="cli", output=kw.get("output"))
    s.doc = doc
    s.pdf_bytes = doc.tobytes()
    s.pdf_name = kw.get("pdf_name", "document.pdf")
    s.assets = dict(assets)
    s.raw_assets = {n: a.path.read_bytes() for n, a in assets.items() if a.path.is_file()}
    s.bg_mode = {n: kw.get("bg_mode", "auto") for n in assets}
    s.bg_tolerance = {n: a.bg_tolerance for n, a in assets.items()}
    s.placements = list(initial or [])
    s.initial_page = page
    return serve(s, open_browser=open_browser, port=kw.get("port", 0))
