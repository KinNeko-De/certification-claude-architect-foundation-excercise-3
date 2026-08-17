#!/usr/bin/env python3
"""Structured data extraction pipeline for recipe HTML files."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema.exceptions import best_match
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
Extract the recipe data from an HTML document.
"""

def build_retry_prompt(
    previous_output: dict[str, Any] | None, error: dict[str, Any] | None
) -> str:
    """Build a follow-up prompt including the document, the failed extraction, and the specific error."""
    assert previous_output is not None and error is not None
    previous_json = json.dumps(previous_output, indent=2, ensure_ascii=False)
    error_path = ".".join(str(p) for p in error["path"]) or "(root)"
    return (
        "A previous extraction attempt produced this JSON, which failed schema validation:\n\n"
        f"{previous_json}\n\n"
        f"Validation error at '{error_path}': {error['message']}\n\n"
        "Correct the extraction so it satisfies the schema. Only change what is needed to "
        "resolve the validation error; keep every other field as accurate as possible. "
    )


async def extract_recipe(html_path: Path, prompt: str) -> dict[str, Any]:
    """Run a single extraction attempt against Claude for the given prompt.

    Opens its own ClaudeSDKClient scoped to this one call, since the client
    keeps the full message history and each attempt must be extracted from a
    clean slate rather than accumulating context across files or retries.
    """
    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        thinking={"type": "disabled"},
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


def first_validation_error(data: dict[str, Any]) -> jsonschema.ValidationError | None:
    """Return the most relevant validation error for data, or None if it validates."""
    validator = jsonschema.Draft202012Validator(VALIDATION_SCHEMA)
    errors = list(validator.iter_errors(data))
    return best_match(errors) if errors else None


def serialize_error(error: jsonschema.ValidationError | None) -> dict[str, Any] | None:
    if error is None:
        return None
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
        current = attempts[i]["validation_error"]
        if current is None:
            continue
        nxt = attempts[i + 1]["validation_error"]
        current_sig = (tuple(current["path"]), current["validator"])
        next_sig = (tuple(nxt["path"]), nxt["validator"]) if nxt else None
        if current_sig != next_sig:
            resolved.append({**current, "resolved_at_attempt": attempts[i + 1]["attempt"]})

    unresolved = []
    if record["final_status"] != "success" and attempts:
        last_error = attempts[-1]["validation_error"]
        if last_error is not None:
            unresolved.append(last_error)

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
    previous_error: dict[str, Any] | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = f"Recipe:\n\n{html}\n\n"
        if attempt > 1:
            prompt = prompt + build_retry_prompt(previous_output, previous_error)

        try:
            data = await extract_recipe(html_path, prompt)
        except RuntimeError as e:
            record["attempts"].append(
                {
                    "attempt": attempt,
                    "output": None,
                    "validation_error": {"message": str(e), "path": [], "validator": "llm_call"},
                }
            )
            break

        error = first_validation_error(data)
        serialized_error = serialize_error(error)
        record["attempts"].append(
            {"attempt": attempt, "output": data, "validation_error": serialized_error}
        )

        if error is None:
            record["final_status"] = "success"
            record["output"] = data
            break

        previous_output = data
        previous_error = serialized_error

    record["resolved_errors"], record["unresolved_errors"] = classify_errors(record)
    return record


async def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    recipe_files = [
        "Chocolate Crinkles von amerikanisch-kochenDE.body.html",
        "Hefeklöße.body.html",
        "Klassisches Jägerschnitzel mit Pilzrahmsoße.body.html",
        "Tofu-Gyros Pita mit veganem Tzatziki _ Einfaches Rezept _ Zucker&Jagdwurst.body.html",
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
