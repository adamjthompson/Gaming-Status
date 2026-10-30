"""Checks relative links in readme.md and docs/**/*.md, plus mkdocs.yml's
nav entries: every linked file must exist, and every #anchor must match a
heading in the target page (using MkDocs' default heading slugs).

`mkdocs build --strict` already covers docs/ itself; this also covers
readme.md, which MkDocs doesn't build. Run from the repository root:

    python scripts/check_doc_links.py

Exits non-zero if anything is broken.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_BLOCK = re.compile(r"```.*?```", re.S)
LINK = re.compile(r"\]\(([^)\s]+)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
NAV_ENTRY = re.compile(r":\s*(\S+\.md)\s*$", re.M)


def slug(heading: str) -> str:
    heading = re.sub(r"[`*_]", "", heading).strip().lower()
    heading = re.sub(r"[^\w\s-]", "", heading)
    return re.sub(r"\s+", "-", heading)


def anchors(path: Path) -> set[str]:
    found, in_code = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code and (match := HEADING.match(line)):
            found.add(slug(match.group(2)))
    return found


def main() -> int:
    errors: list[str] = []
    checked = 0
    pages = sorted((ROOT / "docs").rglob("*.md")) + [ROOT / "readme.md"]
    for page in pages:
        text = CODE_BLOCK.sub("", page.read_text(encoding="utf-8"))
        for target in LINK.findall(text):
            if re.match(r"[a-z]+:", target):  # http:, https:, mailto:, ...
                continue
            checked += 1
            path, _, anchor = target.partition("#")
            resolved = (page.parent / path).resolve() if path else page
            where = page.relative_to(ROOT)
            if not resolved.exists():
                errors.append(f"{where}: missing file {target}")
            elif anchor and resolved.suffix == ".md" and anchor not in anchors(resolved):
                errors.append(f"{where}: missing anchor {target}")
    for entry in NAV_ENTRY.findall((ROOT / "mkdocs.yml").read_text(encoding="utf-8")):
        checked += 1
        if not (ROOT / "docs" / entry).exists():
            errors.append(f"mkdocs.yml: nav entry {entry} doesn't exist")

    for error in errors:
        print(error)
    print(f"{checked} links checked, {len(errors)} broken")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
