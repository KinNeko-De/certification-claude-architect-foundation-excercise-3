#!/usr/bin/env python3
"""Structured data extraction pipeline for recipe HTML files."""

import asyncio
import json
from pathlib import Path
from typing import Any

import jsonschema
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

ROOT = Path(__file__).parent
RECIPES_DIR = ROOT / "recipes"
SCHEMA_PATH = ROOT / "schemas" / "recipe-extraction.schema.json"
VALIDATION_SCHEMA_PATH = ROOT / "schemas" / "recipe-extraction.validation.schema.json"
OUTPUT_DIR = ROOT / "output"

_raw_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SCHEMA = {k: v for k, v in _raw_schema.items() if k not in ("$schema", "$id")}
VALIDATION_SCHEMA = json.loads(VALIDATION_SCHEMA_PATH.read_text(encoding="utf-8"))

system_prompt = """
Extract the recipe data from the following HTML document.
"""


async def extract_recipe(html_path: Path) -> dict[str, Any]:
    """Extract structured recipe data from a single recipe HTML file.

    Opens its own ClaudeSDKClient scoped to this one call, since the client
    keeps the full message history and each recipe must be extracted from a
    clean slate rather than accumulating context across files.
    """
    html = html_path.read_text(encoding="utf-8")

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        thinking={"type": "disabled"},
        effort="low",
        system_prompt=system_prompt,
        tools=[],
        output_format={"type": "json_schema", "schema": SCHEMA},
    )
    prompt = f"Extract the recipe data from the following HTML document.\n\n{html}"

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(
                        f"Extraction failed for {html_path.name}: {message.result}"
                    )
                return message.structured_output

    raise RuntimeError(f"No result received for {html_path.name}")


def validate_recipe(data: dict[str, Any], html_path: Path) -> None:
    """Validate extracted recipe data against the full validation schema."""
    try:
        jsonschema.validate(data, VALIDATION_SCHEMA)
    except jsonschema.ValidationError as e:
        raise RuntimeError(
            f"Schema validation failed for {html_path.name}: {e.message}"
        ) from e


async def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    recipe_files = [
        "Chocolate Crinkles von amerikanisch-kochenDE.body.html",
        "Hefeklöße.body.html",
        "Klassisches Jägerschnitzel mit Pilzrahmsoße.body.html",
        "Tofu-Gyros Pita mit veganem Tzatziki _ Einfaches Rezept _ Zucker&Jagdwurst.body.html",
    ]
    recipe_paths = [RECIPES_DIR / name for name in recipe_files]
    for html_path in recipe_paths:
        print(f"Extracting {html_path.name}...")
        data = await extract_recipe(html_path)
        validate_recipe(data, html_path)

        output_name = html_path.name.removesuffix(".body.html") + ".json"
        output_path = OUTPUT_DIR / output_name
        output_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Wrote {output_path.name}")


if __name__ == "__main__":
    asyncio.run(main())
