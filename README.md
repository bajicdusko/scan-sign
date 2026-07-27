# scan-sign

Drop a signature and a stamp onto a PDF, pick the spot visually, save.
Optionally re-render the result so it looks like it came off a scanner — slight skew, a little
sensor noise, soft corner shading, mild JPEG recompression. Tuned for a current office
machine rather than a 2003 fax; push `--scan-strength` up if you want it grubbier.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The `./scan-sign` launcher uses that venv, so nothing needs to be activated.

## Two ways to run it

### 1. Browser app — everything in the page

```bash
./scan-sign
```

Opens a local page where you choose the PDF, the signature and the stamp from your
filesystem, place them, and hit **Download signed PDF**. Nothing is written to disk by the
tool itself; the file lands in your normal downloads folder as `<name>-signed.pdf`.
**Quit** stops the server.

Files never leave the machine — the server binds to `127.0.0.1` on a random free port
(`--port` to pin it).

### 2. From the command line

```bash
./scan-sign contract.pdf -s signature.png -t stamp.png
```

Same page, but the files are already loaded and **Save to disk & close** writes
`contract-signed.pdf` (or `-o …`) and returns you to the terminal. The download button
works here too.

## Placing

- **click** the page to drop the selected overlay, **drag** to move, **wheel** or the
  size slider to resize, corner handle to scale
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

`--scan` turns pages into images — the text stops being selectable. That is the point, but
skip it if you need a text-searchable PDF.

## Repeat the same layout

```bash
./scan-sign a.pdf -s sig.png -t stamp.png --save-layout mine.json      # place once
./scan-sign b.pdf -s sig.png -t stamp.png --layout mine.json --no-gui  # reuse, no UI
```

`--layout` without `--no-gui` opens the picker pre-populated. `--no-gui` with no layout falls
back to an automatic bottom-of-the-last-page placement.

## Images

PNGs with alpha are used as-is. A photo or scan of a signature on paper gets the paper keyed
out automatically: the paper colour is measured locally across the image, so grey, yellowed and
unevenly lit scans key out cleanly instead of leaving a tinted rectangle. Toggle it per overlay
with **cut paper background**, and use the **tolerance** slider next to it (or `--bg-tolerance`,
default 34) if some paper survives — raise it — or if light ink gets eaten — lower it.
`--remove-bg auto|always|never` controls whether keying runs at all. Overlays are trimmed to
their ink before placing.

Default sizes are 150 pt wide for the signature and 120 pt for the stamp; override with
`--signature-width` / `--stamp-width` (in mm) or resize in the page.

## Hosting it

`python -m scan_sign.server` runs the same UI as a public service. There is no login: a visitor
types a name, which starts a private session for that browser. Sessions are held in memory only,
are never resumed — loading the page again always starts a fresh one — and the *save to disk* and
*quit* endpoints are switched off so a visitor can't write to the host or stop the process.
Sessions are dropped on **New**, after 30 minutes idle, or when the 40-session cap evicts them.

The name is a label, not a credential. It identifies your session in the UI; it does not protect
anything, and it can't be used to get back into a session later.

| env var | default | purpose |
| --- | --- | --- |
| `PORT` | `8080` | listen port |
| `SCAN_SIGN_MAX_UPLOAD_MB` | `25` | per-file upload cap |
| `SCAN_SIGN_MAX_PAGES` | `40` | reject PDFs longer than this |

A `railway.json` and `Procfile` are included, so `railway up` deploys it as-is.

**Anyone who can reach the URL can use it.** People upload their signature to this — about the
most forgeable thing they own. Nothing is written to the server's disk and sessions are isolated
and short-lived, but a public instance is still a public instance: put it behind your own
network controls if that matters, and run it locally for anything real.

## Notes

- `-p/--page` picks the page the UI opens on (1-based). Default is the last page.
- `--no-browser` prints the local URL instead of opening a tab.
- Placements live in the browser tab — reloading the page loses them (it asks first).
- `--picker tk` uses a native Tkinter window instead. It has the same placement features but
  no file loading or download, and it is untested here — macOS system Python ships a
  deprecated Tk 8.5. The browser UI is the default for that reason.
