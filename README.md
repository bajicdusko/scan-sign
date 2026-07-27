# scan-sign

Put a signature and a stamp onto a PDF. Place them by eye in a browser page, then save or
download the result.

Optionally re-render the whole thing so it looks like it came off a scanner — slight skew, a
little sensor noise, soft corner shading, mild JPEG recompression. Tuned for a current office
machine rather than a 2003 fax; turn the strength up if you want it grubbier.

**Try it:** [scan-sign-production.up.railway.app](https://scan-sign-production.up.railway.app) —
type a name, load your files, download the result. Nothing is stored; see
[Hosting it](#hosting-it) before using it for anything real.

## Install

```bash
git clone https://github.com/bajicdusko/scan-sign && cd scan-sign
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The `./scan-sign` launcher uses that venv, so nothing needs activating. Or install the command
straight from the repo:

```bash
pipx install git+https://github.com/bajicdusko/scan-sign     # then just: scan-sign
```

Python 3.9+. The only dependencies are PyMuPDF, Pillow and NumPy.

## Three ways to run it

### 1. Browser app — everything in the page

```bash
./scan-sign
```

Opens a local page where you choose the PDF, the signature and the stamp from your filesystem,
place them, and hit **Download signed PDF**. The file lands in your normal downloads folder as
`<name>-signed.pdf`. **Quit** stops the server.

Files never leave the machine — it binds to `127.0.0.1` on a random free port (`--port` to pin
one, `--no-browser` to print the URL instead of opening a tab).

### 2. From the command line

```bash
./scan-sign contract.pdf -s signature.png -t stamp.png
```

Same page, but the files are already loaded and **Save to disk & close** writes
`contract-signed.pdf` (or `-o …`) and returns you to the terminal. The download button works
here too.

### 3. Hosted, for other people

```bash
python -m scan_sign.server
```

The public flavour: visitors name their own session. See [Hosting it](#hosting-it).

## Placing

- **click** the page to drop the selected overlay, **drag** to move, **wheel** or the size
  slider to resize, corner handle to scale
- `[` / `]` rotate (hold ⇧ for 5° steps), arrows nudge, ⌫ deletes, `tab` switches
  signature/stamp
- ◀ ▶ change page — overlays stay on the page you dropped them on
- what you see is what gets written: position, size and rotation match the output exactly

## Scanned look

Tick **fake a scan** in the page, or from the CLI:

```bash
./scan-sign contract.pdf -s signature.png -t stamp.png --scan --scan-strength 0.6
```

| flag | effect |
| --- | --- |
| `--scan` | rasterize each page and add scanner artifacts |
| `--scan-strength 0..1` | how much noise / blur / contrast (default `0.5`; `1.0` is visibly grubby) |
| `--rotate 1.4` | max random page skew in degrees (default `0.8`) |
| `--keep-edges` | show the paper edge against the scanner lid instead of cropping into the page |
| `--grayscale` | grayscale scan |
| `--no-corner-damage` | skip the dark smudge in a random corner |
| `--scan-dpi 300` | rasterization DPI (default `200`) |
| `--seed 7` | reproducible artifacts |

`--scan` turns pages into images — the text stops being selectable. That is the point, but skip
it if you need a text-searchable PDF.

## Images

PNGs with alpha are used as-is. A photo or scan of a signature on paper gets the paper keyed out
automatically: the paper colour is measured *locally* across the image, so grey, yellowed and
unevenly lit scans key out cleanly instead of leaving a tinted rectangle behind the signature.
Partly transparent edge pixels have the paper subtracted back out, so there is no coloured halo
around the strokes.

Toggle it per overlay with **cut paper background**, and use the **tolerance** slider beside it
(or `--bg-tolerance`, default 34): raise it if some paper survives, lower it if light ink gets
eaten. `--remove-bg auto|always|never` controls whether keying runs at all — `auto` means "only
when the image has no alpha channel of its own". Overlays are trimmed to their ink before
placing.

Default sizes are 150 pt wide for the signature and 120 pt for the stamp; override with
`--signature-width` / `--stamp-width` (in mm) or just resize in the page.

## Repeat the same layout

```bash
./scan-sign a.pdf -s sig.png -t stamp.png --save-layout mine.json      # place once
./scan-sign b.pdf -s sig.png -t stamp.png --layout mine.json --no-gui  # reuse, no UI
```

`--layout` without `--no-gui` opens the picker pre-populated. `--no-gui` with no layout falls
back to an automatic bottom-of-the-last-page placement.

## Hosting it

`python -m scan_sign.server` runs the same UI as a public service. There is no login: a visitor
types a name, which starts a private session for that browser.

- Sessions live **in memory only** — nothing is ever written to the server's disk.
- Sessions are **never resumed**. Loading the page ends whatever that browser still held and
  asks for a name again, so coming back always starts fresh and nobody inherits the files of
  whoever used the browser before them.
- They are dropped on **New**, after 30 minutes idle, or when the 40-session cap evicts the
  oldest.
- *Save to disk* and *quit* are not routed at all, so a visitor can't write to the host or stop
  the process.
- Uploads, page counts and the render DPI are capped, so one request can't exhaust the container.

The name is a label, not a credential. It identifies your session in the UI; it protects nothing
and can't be used to get back into a session later.

| env var | default | purpose |
| --- | --- | --- |
| `PORT` | `8080` | listen port |
| `SCAN_SIGN_MAX_UPLOAD_MB` | `25` | per-file upload cap |
| `SCAN_SIGN_MAX_PAGES` | `40` | reject PDFs longer than this |
| `SCAN_SIGN_SUPPORT_URL` | my Buy Me a Coffee page | the tip link offered on the landing page and next to a finished download; set it empty to drop both |

Point `SCAN_SIGN_SUPPORT_URL` at your own Buy Me a Coffee page and the landing page's footer bar
renders their button for your account. Point it anywhere else and it falls back to a plain link,
so a tip never reaches an account you didn't choose. Empty it and the ask leaves the bar
altogether — including the request to their CDN.

A `railway.json` and `Procfile` are included, so `railway up` deploys it as-is.

**Anyone who can reach the URL can use it.** People upload their signature to this — about the
most forgeable thing they own. Sessions are isolated, short-lived and never hit disk, but a
public instance is still a public instance: put it behind your own network controls if that
matters, and run it locally for anything real.

## Notes

- `-p/--page` picks the page the UI opens on (1-based). Default is the last page.
- Placements live in the browser tab. Reloading loses them — the page asks first — and on a
  hosted instance a reload also ends the session.
- `--picker tk` opens a native Tkinter window instead of the browser page. Same placement
  features, but no file loading and no download, and it is untested: macOS system Python ships
  a deprecated Tk 8.5. The browser UI is the default for that reason.

## License

MIT — see [LICENSE](LICENSE).
