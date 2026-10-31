#!/usr/bin/env python3
"""Put each asset's content hash in its URL in Website/index.html.

Cloudflare may keep serving an old /site.js or /site.css for hours whatever _headers says, so
the page names a file by its contents: /site.js?v=<hash>. An edit changes the hash, the HTML
(always revalidated) points at a new URL, and no stale copy can match it.

  scripts/stamp-assets.py            rewrite the URLs in index.html
  scripts/stamp-assets.py --check    exit 1 if they are stale (the pre-commit hook uses this
                                     after stamping, and CI can too)
"""

import hashlib
import re
import sys
from pathlib import Path

WEBSITE = Path(__file__).resolve().parent.parent / "Website"
PAGE = WEBSITE / "index.html"
ASSETS = ("site.css", "site.js")


def stamped(html):
    for name in ASSETS:
        digest = hashlib.sha256((WEBSITE / name).read_bytes()).hexdigest()[:10]
        html, count = re.subn(rf'(["\']/{re.escape(name)})(?:\?v=[0-9a-f]+)?(["\'])', rf"\1?v={digest}\2", html)
        if count != 1:
            sys.exit(f"error: expected one reference to /{name} in {PAGE.name}, found {count}")
    return html


def main():
    current = PAGE.read_text()
    updated = stamped(current)
    if "--check" in sys.argv[1:]:
        if updated != current:
            sys.exit("index.html has stale asset hashes: run scripts/stamp-assets.py")
        return
    if updated != current:
        PAGE.write_text(updated)
        print("stamped", ", ".join(ASSETS))


if __name__ == "__main__":
    main()
