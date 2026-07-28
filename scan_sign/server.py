"""Hosted entry point: `python -m scan_sign.server`.

Reads the platform's PORT. Opening the app starts a session; sessions are held in memory,
never resumed, and dropped when idle.
"""

from __future__ import annotations

import os

from .webpicker import serve_hosted


def main() -> None:
    serve_hosted(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
    )


if __name__ == "__main__":
    main()
