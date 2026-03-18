# Clause Library Schema (JSONL/CSV)

Each row is one retrievable clause chunk.

## Fields
- id: unique identifier
- source: template source (OneNDA / EDGAR)
- source_doc: document name/version
- nda_type: Mutual or Unilateral
- jurisdiction: optional; blank when placeholder-based
- clause_name: internal chunk label
- clause_type: normalized clause category for retrieval
- variant_id: clause variant bucket for controlled drafting
- text: clause text
- placeholders_present: list (JSONL) or semicolon list (CSV)

## Notes
- {{GOVERNING_LAW}} and {{DISPUTE_RESOLUTION_METHOD}} are kept as placeholders (no defaults).
