# Disclaimer

> This repository is my personal working copy based on course material for [Claude Certified Architect - Foundation](https://anthropic-partners.skilljar.com/page/partner-certifications). It is not an official Anthropic source or endorsed resource for your training.

# Scope

This repository implements Exercise 3: Build a Structured Data Extraction Pipeline from the [Claude Certified Architect exam guide] (version 0.2)

# Testdata

1. Download recipes from multiples website sources.
2. use the tools [extract_body](tools/extract_body.py) and then [strip](tools/strip_to_save_tokens.py) html tag to save tokens.

# Findings

## Nullable fields vs. fabrication (2026-08-14)

Task: verify the model returns `null` for fields absent from the source document rather than fabricating a value. Cross-checked the 4 extracted recipes in [output/](output/) against their source HTML in [recipes/](recipes/).

Working as designed:
- `calories_kcal` is correctly `null` for the Tofu-Gyros recipe, which has no calorie figure anywhere in its source.
- The enum `other` + free-text detail pattern works end-to-end: the Tofu-Gyros recipe correctly emits `{"value": "other", "detail": "glutenfrei"}` for a dietary flag outside the fixed enum.

Fabrication found in `servings` (1 of 4 documents):
- **Chocolate Crinkles**: extracted `1`, but the source states no yield at all. The `1` is the default value of an ingredient-quantity-scaler widget (`<input type="number" id="quantity" ... value="1">`), also reflected in a `?portionen=1` print-link URL — not a stated serving count.
- Hefeklöße (`10`, matches "Zutaten für 10 Stück"), Klassisches Jägerschnitzel (`4`, matches an explicit "PORTIONEN 4 Personen" recipe-card field), and Tofu-Gyros (`3`, matches "3 Portionen") are all correct.

Related but lower-severity: `prep_time_minutes` is never fabricated (every value traces to a real number in the source), but is mapped inconsistently when a source splits time into multiple labeled buckets. Chocolate Crinkles picked "Arbeitszeit" (active time, 30 of a 40-minute Gesamtzeit); Jägerschnitzel and Hefeklöße both picked the total ("Zeit gesamt" / "Gesamtzeit").

Planned follow-up:
- `prep_time_minutes`: add a few-shot example to the prompt to make "always use total time" an explicit, consistent rule.
- `servings`: use as the worked example for field-level confidence scoring — a field where the model produces a plausible-looking but unstated value instead of `null`.

## Non-instruction text extracted as a step (2026-08-18)

Task: added a `steps` field to both schemas (list of preparation instructions, see [schemas/recipe-extraction.schema.json](schemas/recipe-extraction.schema.json)).

For one recipe the pipeline appended the source document's closing pleasantry, "Enjoy your meal.", as the final entry in `steps` — it's not a preparation instruction.

Planned follow-up:
- `steps`: add a few-shot example to the field description showing that closing remarks/pleasantries are excluded, and re-run to confirm it resolves this without suppressing genuine final steps (e.g. plating/garnishing instructions, which should stay in).