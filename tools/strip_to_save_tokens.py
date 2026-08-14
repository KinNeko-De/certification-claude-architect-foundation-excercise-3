#!/usr/bin/env python3
"""Strip everything from extracted recipe body HTML files so my usage limit does not ran off"""

import re
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "recipes" / "raw"
OUTPUT_DIR = Path(__file__).parent.parent / "recipes"
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STYLES_TAG_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
SPARK_RE = re.compile(r"<spark-\b[^>]*>.*?</spark-\b[^>]*>", re.IGNORECASE | re.DOTALL)
IMAGES_RE = re.compile(r"<img\b[^>]*>.*?</img>", re.IGNORECASE | re.DOTALL)
IMAGES_2_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
SVG_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.IGNORECASE | re.DOTALL)
STYLES_RE = re.compile(r'\s+style\s*=\s*(["\'])(.*?)\1',re.IGNORECASE | re.DOTALL)
CLASS_RE = re.compile(r'\s+class\s*=\s*(["\'])(.*?)\1',re.IGNORECASE | re.DOTALL)
ARIA_RE = re.compile(r'\s+aria-[a-zA-Z]+\s*=\s*(["\'])(.*?)\1',re.IGNORECASE | re.DOTALL)
DATA_RE = re.compile(r'\s+data-[a-zA-Z-]+',re.IGNORECASE | re.DOTALL)

def strip_scripts(html: str) -> str:
    return SCRIPT_RE.sub("", html)

def strip_comments(html: str) -> str:
    return COMMENT_RE.sub("", html)

def strip_styles_tag(html: str) -> str:
    return STYLES_TAG_RE.sub("", html)

def strip_images_tag(html: str) -> str:
    stripped = IMAGES_RE.sub("", html)
    return IMAGES_2_RE.sub("", stripped)

def strip_svg(html: str) -> str:
    return SVG_RE.sub("", html)

def strip_spark(html: str) -> str:
    return SPARK_RE.sub("", html)

def strip_styles_attribute(html: str) -> str:
    return STYLES_RE.sub("", html)

def strip_classes_attribute(html: str) -> str:
    return CLASS_RE.sub("", html)

def strip_aria_attribute(html: str) -> str:
    return ARIA_RE.sub("", html)

def strip_data_attribute(html: str) -> str:
    return DATA_RE.sub("", html)

def main() -> None:
    for input_path in sorted(RAW_DIR.glob("*.body.html")):
        if not input_path.is_file():
            continue
        html = input_path.read_text(encoding="utf-8")
        stripped = strip_scripts(html)
        stripped = strip_comments(stripped)
        stripped = strip_styles_tag(stripped)
        stripped = strip_images_tag(stripped)
        stripped = strip_svg(stripped)
        stripped = strip_spark(stripped)
        stripped = strip_styles_attribute(stripped)
        stripped = strip_classes_attribute(stripped)
        stripped = strip_aria_attribute(stripped)
        stripped = strip_data_attribute(stripped)
        stripped = stripped.replace('\t', ' ')
        stripped = re.sub(r' +',  ' ', stripped)
        output_path = OUTPUT_DIR / input_path.name
        output_path.write_text(stripped, encoding="utf-8")
        print(f"Wrote {output_path.name}")


if __name__ == "__main__":
    main()
