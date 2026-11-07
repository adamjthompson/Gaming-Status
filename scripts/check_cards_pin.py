"""Checks the public page's pinned cards fallback (public_page.py's
CARDS_CDN_TAG / CARDS_CDN_INTEGRITY): the file jsDelivr serves for that tag
must match the Subresource Integrity hash, or browsers refuse to run it and
the public page shows no cards for anyone without the cards installed as a
dashboard resource.

Also warns (without failing) when a newer Gaming Status Cards release
exists, as a reminder to bump the pin. Run from the repository root:

    python scripts/check_cards_pin.py

Exits non-zero if the hash doesn't match or the file can't be fetched.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_PAGE = ROOT / "custom_components" / "gaming_status" / "public_page.py"
RELEASES_API = (
    "https://api.github.com/repos/adamjthompson/Gaming-Status-Cards/releases?per_page=1"
)


def _constant(source: str, name: str) -> str:
    """The string value of `name = (...)` in public_page.py, joining
    implicitly concatenated literals and filling in {CARDS_CDN_TAG}."""
    match = re.search(
        rf"^{name} = \(?\s*((?:f?\"[^\"]*\"\s*)+)\)?", source, re.MULTILINE
    )
    if not match:
        sys.exit(f"Couldn't find {name} in {PUBLIC_PAGE}")
    value = "".join(re.findall(r"\"([^\"]*)\"", match.group(1)))
    if "{CARDS_CDN_TAG}" in value:
        value = value.replace("{CARDS_CDN_TAG}", _constant(source, "CARDS_CDN_TAG"))
    return value


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(  # noqa: S310 -- fixed https URLs
        url, headers={"User-Agent": "gaming-status-ci", **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read()


def main() -> int:
    source = PUBLIC_PAGE.read_text(encoding="utf-8")
    tag = _constant(source, "CARDS_CDN_TAG")
    url = _constant(source, "CARDS_CDN_FALLBACK")
    expected = _constant(source, "CARDS_CDN_INTEGRITY")

    try:
        body = _get(url)
    except OSError as err:
        print(f"Couldn't fetch {url}: {err}")
        return 1
    actual = "sha384-" + base64.b64encode(hashlib.sha384(body).digest()).decode()
    if actual != expected:
        print(
            f"CARDS_CDN_INTEGRITY doesn't match {url}\n"
            f"  expected: {expected}\n  actual:   {actual}"
        )
        return 1
    print(f"Pinned cards {tag}: integrity OK")

    # Best effort: a reminder, never a failure (API limits, offline runs).
    try:
        token = os.environ.get("GITHUB_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        latest = json.loads(_get(RELEASES_API, headers))[0]["tag_name"]
    except (OSError, ValueError, LookupError):
        return 0
    if latest != tag:
        print(
            f"::warning::Public page pins Gaming Status Cards {tag}, but {latest} "
            "is the newest cards release -- bump CARDS_CDN_TAG and "
            "CARDS_CDN_INTEGRITY in public_page.py."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
