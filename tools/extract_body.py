#!/usr/bin/env python3
"""Extract the content inside the <body> tag from raw recipe HTML files."""

import re
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "recipes" / "raw"
BODY_RE = re.compile(r"<body\b[^>]*>(.*)</body>", re.IGNORECASE | re.DOTALL)


def extract_body(html: str) -> str:
    match = BODY_RE.search(html)
    if not match:
        raise ValueError("No <body> tag found")
    return match.group(1)


def main() -> None:
    for input_path in sorted(RAW_DIR.glob("*.htm")):
        if not input_path.is_file():
            continue
        html = input_path.read_text(encoding="utf-8")
        body = extract_body(html)
        output_path = input_path.with_suffix("").with_suffix(".body.html")
        output_path.write_text(body, encoding="utf-8")
        print(f"Wrote {output_path.name}")


if __name__ == "__main__":
    main()
