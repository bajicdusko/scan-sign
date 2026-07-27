"""Hosted entry point: `python -m scan_sign.server`.

Reads the platform's PORT. Set SCAN_SIGN_PASSWORD to put the instance behind basic auth —
worth doing for anything reachable from the internet, since people upload their signature to it.
"""

from __future__ import annotations

import os

from .webpicker import serve_hosted


def main() -> None:
    serve_hosted(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        password=os.environ.get("SCAN_SIGN_PASSWORD") or None,
    )


if __name__ == "__main__":
    main()
