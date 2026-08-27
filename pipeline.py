#!/usr/bin/env python3
"""Structured data extraction pipeline for recipe HTML files."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

ROOT = Path(__file__).parent
RECIPES_DIR = ROOT / "recipes"
SCHEMA_PATH = ROOT / "schemas" / "recipe-extraction.schema.json"
VALIDATION_SCHEMA_PATH = ROOT / "schemas" / "recipe-extraction.validation.schema.json"
OUTPUT_DIR = ROOT / "output"
LOGS_DIR = OUTPUT_DIR / "logs"

MAX_ATTEMPTS = 3

_raw_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SCHEMA = {k: v for k, v in _raw_schema.items() if k not in ("$schema", "$id")}
VALIDATION_SCHEMA = json.loads(VALIDATION_SCHEMA_PATH.read_text(encoding="utf-8"))

system_prompt = """
# Instruction
Extract the recipe data from an HTML document.

# Prepartion steps
Split the prepartion description into multiple step that can execute with one action

# Dietary flags
Goal: The user filter the recipe by their diet or restrictions.

Base dietary_flags on both the ingredients and the description.

Only add a flag if it names a diet, restriction, or trend that someone would actively
filter recipes by (good example: 'high-protein' is a current trend).

Do not add a flag that merely states a nutrition fact about this specific recipe, even if
true (bad examples: "egg-containing", "high-sugar")

The fixed values (every value except 'other') may be inferred logically from the ingredients
or description, even if not stated explicitly. Only use 'other' when the
description explicitly name a diet, restriction, or trend that isn't one of the fixed values.

<example>
  <description>
    Vegane Schokomuffins
  </description>
  <ingredient>
    Trockene Inhaltsstoffe:
      120 g Hafermehl glutenfrei
      70-100 g Kokosblütenzucker
      50 g ungesüßtes Kakaopulver
      1 TL Backpulver
      ¼ TL Natron
      ¼ TL Salz
      90 g vegane Schokodrops
    Feuchte Zutaten:
      180 ml Kokosmilch
      160 g Apfelmus ungesüßt
      65 g Sonnenblumenkernmus
      1 EL Apfelessig
      1 TL Vanilleextrakt
  </ingredient> 

  <output>
    "dietary_flags": [
      {
        "value": "vegetarian",
        "detail": null
      },
      {
        "value": "vegan",
        "detail": null
      },
      {
        "value": "other",
        "detail": "gluten-free"
      }
    ]
  </output>
</example>

<example>
  <description>
    Leckere Steaks
  </description>
  <ingredient>
    1 Steak
    Salt
    Pepper
  </ingredient>

  <output>
    "dietary_flags": [
      {
        "value": "low-carb",
        "detail": null
      },
      {
        "value": "ketogenic",
        "detail": null
      },
      {
        "value": "high-protein",
        "detail": null
      },
      {
        "value": "paleo",
        "detail": null
      }
    ]
  </output>
</example>

<example>
  <description>
    Masala Egg Fry
    in Breakfast, easy, ovo vegetarian, Starters
  </description>
  <ingredient>
    6 eggs
    cumin seeds a pinch
    1 tablespoon oil
    2 sprigs curry leaves
    salt
  </ingredient>

  <output>
    "dietary_flags": [
      {
        "value": "vegetarian",
        "detail": null
      },
      {
        "value": "low-carb",
        "detail": null
      },
      {
        "value": "ketogenic",
        "detail": null
      },
      {
        "value": "paleo",
        "detail": null
      },
      {
        "value": "high-protein",
        "detail": null
      },
      {
        "value": "other",
        "detail": "ovo-vegetarian"
      }
    ]
  </output>
</example>

# Confidence
Goal: route low-confidence extractions to human review, so scores must reflect genuine
uncertainty rather than being uniformly high.

- 1.0: the value is explicitly and unambiguously stated in the source document.
- Mid-range: the value is a reasonable inference, not stated outright (e.g. a dietary flag
  inferred from the ingredients rather than named in the text).
- Near 0.0: the value is essentially a guess, or was extracted from something that only
  superficially resembles the requested fact (e.g. a UI control's default value).

A `null` value should itself score high when the source clearly does not mention the fact
at all, and lower when it is unclear whether the fact is truly absent or just wasn't found.

Use the full range. Most extractions will not be a perfect 1.0 - reserve that for text
copied verbatim from an explicit, unambiguous statement in the source, and score everything
else accordingly.

In addition to the per-field scores, also give an `overall_confidence` for the extraction
as a whole. Form this as your own independent, holistic judgment - do not simply average or
copy the per-field scores. Weigh things like the source document's overall clarity and how
many fields required inference rather than being explicitly stated.
"""

def build_retry_prompt(
    previous_output: dict[str, Any] | None, errors: list[dict[str, Any]] | None
) -> str:
    """Build a follow-up prompt including the document, the failed extraction, and the specific errors."""
    assert previous_output is not None and errors
    previous_json = json.dumps(previous_output, indent=2, ensure_ascii=False)
    error_lines = "\n".join(
        f"- '{'.'.join(str(p) for p in error['path']) or '(root)'}': {error['message']}"
        for error in errors
    )
    return (
        "A previous extraction attempt produced this JSON, which failed schema validation:\n\n"
        f"{previous_json}\n\n"
        f"Validation errors:\n{error_lines}\n\n"
        "Correct the extraction so it satisfies the schema. Only change what is needed to "
        "resolve the validation errors; keep every other field as accurate as possible. "
    )


async def extract_recipe(html_path: Path, prompt: str) -> dict[str, Any]:
    """Run a single extraction attempt against Claude for the given prompt.

    Opens its own ClaudeSDKClient scoped to this one call, since the client
    keeps the full message history and each attempt must be extracted from a
    clean slate rather than accumulating context across files or retries.
    """
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        # thinking={"type": "disabled"},
        thinking={"type": "adaptive"},
        effort="low",
        system_prompt=system_prompt,
        tools=[],
        output_format={"type": "json_schema", "schema": SCHEMA},
    )

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


def validation_errors(data: dict[str, Any]) -> list[jsonschema.ValidationError]:
    """Return all distinct validation errors for data (deduped by path + validator)."""
    validator = jsonschema.Draft202012Validator(VALIDATION_SCHEMA)
    seen = set()
    distinct = []
    for error in validator.iter_errors(data):
        signature = (tuple(error.absolute_path), error.validator)
        if signature not in seen:
            seen.add(signature)
            distinct.append(error)
    return distinct


def serialize_error(error: jsonschema.ValidationError) -> dict[str, Any]:
    return {
        "message": error.message,
        "path": list(error.absolute_path),
        "validator": error.validator,
    }


def classify_errors(record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Diff consecutive attempts' errors to tell resolved-via-retry apart from unresolved ones."""
    attempts = record["attempts"]
    resolved = []
    for i in range(len(attempts) - 1):
        next_sigs = {
            (tuple(error["path"]), error["validator"])
            for error in attempts[i + 1]["validation_errors"]
        }
        for error in attempts[i]["validation_errors"]:
            sig = (tuple(error["path"]), error["validator"])
            if sig not in next_sigs:
                resolved.append({**error, "resolved_at_attempt": attempts[i + 1]["attempt"]})

    unresolved = []
    if record["final_status"] != "success" and attempts:
        unresolved = list(attempts[-1]["validation_errors"])

    return resolved, unresolved


async def extract_and_validate_with_retries(html_path: Path) -> dict[str, Any]:
    """Extract and validate a recipe, retrying on validation failure up to MAX_ATTEMPTS times."""
    html = html_path.read_text(encoding="utf-8")
    record: dict[str, Any] = {
        "file": html_path.name,
        "final_status": "failed",
        "output": None,
        "attempts": [],
    }

    previous_output: dict[str, Any] | None = None
    previous_errors: list[dict[str, Any]] | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = f"Recipe:\n\n{html}\n\n"
        if attempt > 1:
            prompt = prompt + build_retry_prompt(previous_output, previous_errors)

        try:
            data = await extract_recipe(html_path, prompt)
        except RuntimeError as e:
            record["attempts"].append(
                {
                    "attempt": attempt,
                    "output": None,
                    "validation_errors": [
                        {"message": str(e), "path": [], "validator": "llm_call"}
                    ],
                }
            )
            break

        errors = validation_errors(data)
        serialized_errors = [serialize_error(error) for error in errors]
        record["attempts"].append(
            {"attempt": attempt, "output": data, "validation_errors": serialized_errors}
        )

        if not errors:
            record["final_status"] = "success"
            record["output"] = data
            break

        previous_output = data
        previous_errors = serialized_errors

    record["resolved_errors"], record["unresolved_errors"] = classify_errors(record)
    return record


async def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    recipe_files = [
        "Chocolate Crinkles von amerikanisch-kochenDE.body.html",
        "Hefeklöße.body.html",
        "Klassisches Jägerschnitzel mit Pilzrahmsoße.body.html",
        "Tofu-Gyros Pita mit veganem Tzatziki _ Einfaches Rezept _ Zucker&Jagdwurst.body.html",
        "How to Cook Spaghetti Squash - Recipes by Love and Lemons.body.html",
        "Mushy Tapioca (Cassava_Kappa) Recipe _ The take it easy chef.body.html"
    ]
    recipe_paths = [RECIPES_DIR / name for name in recipe_files]

    report_recipes = []
    for html_path in recipe_paths:
        print(f"Extracting {html_path.name}...")
        record = await extract_and_validate_with_retries(html_path)
        report_recipes.append(record)

        if record["final_status"] == "success":
            output_name = html_path.name.removesuffix(".body.html") + ".json"
            output_path = OUTPUT_DIR / output_name
            output_path.write_text(
                json.dumps(record["output"], indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"Wrote {output_path.name} ({len(record['attempts'])} attempt(s))")
        else:
            print(
                f"Failed {html_path.name} after {len(record['attempts'])} attempt(s); "
                "see report for details."
            )

    succeeded = sum(1 for r in report_recipes if r["final_status"] == "success")
    resolved_count = sum(len(r["resolved_errors"]) for r in report_recipes)
    unresolved_count = sum(len(r["unresolved_errors"]) for r in report_recipes)

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "recipes": report_recipes,
        "summary": {
            "total": len(report_recipes),
            "succeeded": succeeded,
            "failed": len(report_recipes) - succeeded,
            "resolved_error_count": resolved_count,
            "unresolved_error_count": unresolved_count,
        },
    }

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = LOGS_DIR / f"{timestamp}_{uuid.uuid4().hex[:8]}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"{succeeded}/{len(report_recipes)} recipes succeeded "
        f"({resolved_count} error(s) resolved via retry, {unresolved_count} unresolved). "
        f"Report: {report_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
