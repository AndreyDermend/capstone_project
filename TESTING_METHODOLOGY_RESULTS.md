# LexiAgent Testing Methodology and Results

Date executed: 2026-04-17

## Purpose

This document summarizes the current test methodology for LexiAgent and the results from the test suites that were executed on April 17, 2026. The goal was to produce evidence that is useful both for technical documentation and for capstone presentation discussion.

The testing strategy intentionally focuses on the system's core reliability claims:

- The system asks follow-up questions instead of guessing.
- The deterministic assembly layer produces complete contracts with no unresolved placeholders.
- The API behaves like an OpenAI-compatible backend for Open WebUI.
- Generated DOCX artifacts are valid, readable, and traceable to source clauses.
- The RAG layer returns complete clause selections and explicit selection metadata.

## Test Methodology

### 1. API contract tests

File:
- `app/test_api.py`

Method:
- Uses `fastapi.testclient.TestClient`
- Avoids Ollama/model dependencies by mocking extraction when needed
- Verifies endpoint shape, response format, error handling, and session isolation

Coverage:
- `GET /health`
- `GET /v1/models`
- welcome response on empty chat payload
- unknown contract type prompt
- streaming SSE format
- non-streaming OpenAI-compatible JSON format
- download 404 behavior
- independent server-side sessions for different conversations
- API key auth reserved as a skipped test until auth middleware is implemented

### 2. Follow-up parsing tests

File:
- `app/test_extraction.py followup`

Method:
- Tests the parser used after the assistant asks for missing fields
- Confirms three supported answer formats:
  - numbered
  - labeled
  - line-by-line
- Confirms partial answers leave the correct fields still missing

### 3. Edge-case validation tests

File:
- `app/test_extraction.py edge`

Method:
- Exercises deterministic safety behavior around verification and assembly
- Confirms conservative handling of missing evidence, unknown keys, Unicode, long values, and zero-value numerics

Coverage:
- empty extraction payload produces all required follow-ups
- unknown injected keys are filtered out by schema validation
- Unicode values survive verification and assembly
- long text values are preserved without breaking assembly
- values without evidence are rejected
- `$0` and `0` numeric-style values are preserved correctly

### 4. DOCX validation tests

File:
- `app/test_extraction.py docx all`

Method:
- Uses complete fixture answers for all four contract types
- Runs assembly with `use_rag=True`
- Generates DOCX plus citation sidecar JSON
- Opens the document with `python-docx`
- Checks for:
  - non-empty file creation
  - Title and Heading 1 structure
  - paragraph count
  - expected party names in the body
  - no unresolved `{{PLACEHOLDER}}`
  - sidecar clause count matching the generated document

### 5. RAG metadata and selection audit

File:
- `app/test_extraction.py rag all`

Method:
- Calls `select_clauses_rag()` directly
- Confirms every clause in assembly order has a result
- Confirms deterministic behavior for single-candidate NDA clauses
- Confirms `method="rag"` is used for multi-variant clause families
- Records whether changing context causes selected variant IDs to change

Important note:
- The current RAG implementation produces complete metadata and valid selections across all contract types.
- In the contrast contexts tested on April 17, 2026, the non-NDA suites did not change selected variant IDs. That is documented as a current limitation rather than hidden or overstated.

## Executed Results

### Summary

Executed suites:
- API tests: 8 passed, 1 skipped
- Follow-up parsing: 4 passed, 0 failed
- Edge-case validation: 6 passed, 0 failed
- DOCX validation: 4 passed, 0 failed
- RAG audit: 4 passed, 0 failed

Executed total:
- 26 passed
- 0 failed
- 1 skipped

Skipped:
- API key authentication enforcement, because `LEXIAGENT_API_KEY` middleware is not implemented yet in `app/api_server.py`

### API Results

Command:

```bash
python app/test_api.py
```

Result:
- 9 tests ran
- 8 passed
- 1 skipped

Observed outcome:
- The API currently behaves correctly as an OpenAI-compatible backend for health checks, model listing, chat completion formatting, SSE streaming, file download errors, and session separation.

### Follow-up Parsing Results

Command:

```bash
python app/test_extraction.py followup
```

Result:
- 4/4 passed

Observed outcome:
- The follow-up loop can safely accept answers in the three user-facing formats planned for the demo and documentation.

### Edge-Case Results

Command:

```bash
python app/test_extraction.py edge
```

Result:
- 6/6 passed

Key findings:
- Empty inputs trigger required follow-ups instead of silent guessing.
- Verification removes unknown keys and rejects answers that lack evidence.
- Unicode and long text survive the pipeline.
- Zero-value numerics are retained instead of being dropped as falsey values.

### DOCX Results

Command:

```bash
python app/test_extraction.py docx all
```

Result:
- 4/4 passed

Per-contract artifact metrics:

| Contract Type | Paragraphs | Clauses |
|---|---:|---:|
| NDA | 56 | 15 |
| Consulting Agreement | 47 | 16 |
| Employment Agreement | 77 | 19 |
| Service Agreement | 67 | 19 |

Observed outcome:
- All four contract types generated valid DOCX files and citation sidecars.
- All tested documents contained title/heading structure and no unresolved placeholders.

### RAG Results

Command:

```bash
python app/test_extraction.py rag all
```

Result:
- 4/4 passed

Per-contract observations:

| Contract Type | Selected Clauses | RAG Clauses | Deterministic Clauses | Variant Changes Under Tested Context Shift |
|---|---:|---:|---:|---:|
| NDA | 15 | 0 | 15 | N/A |
| Consulting Agreement | 16 | 16 | 0 | 0 |
| Employment Agreement | 19 | 19 | 0 | 0 |
| Service Agreement | 19 | 19 | 0 | 0 |

Interpretation:
- The RAG layer is active and returns complete clause metadata for all multi-variant contract types.
- However, under the current contrast prompts/fixtures, the chosen variant IDs did not change. This means LexiAgent can currently demonstrate RAG coverage and traceability, but not yet a strong "different context, different clause selection" story for the non-NDA templates.

## Deliverables Created

### Test files

- `app/test_api.py`
- `app/test_extraction.py`

New `app/test_extraction.py` modes:
- `python app/test_extraction.py e2e`
- `python app/test_extraction.py rag`
- `python app/test_extraction.py edge`
- `python app/test_extraction.py followup`
- `python app/test_extraction.py docx`
- `python app/test_extraction.py full`

### Machine-readable outputs

Generated in `output/`:
- `test_results_followup.json`
- `test_results_edge.json`
- `test_results_docx.json`
- `test_results_rag.json`

## Presentation-Ready Talking Points

### Recommended methodology summary

"I tested the system in layers rather than only with one happy-path demo. I validated the API contract, the follow-up parser, edge-case handling, RAG clause selection metadata, and the final DOCX output."

### Recommended result summary

"On the executed suites, 26 tests passed with zero failures, and one test was intentionally skipped because API key enforcement has not been implemented yet."

### Recommended credibility point

"The strongest result is not just that contracts generate. It is that the safety rails behave correctly: missing evidence becomes a follow-up, unknown keys are filtered out, and generated DOCX files are complete with no unresolved placeholders."

### Recommended honest limitation statement

"The current RAG layer successfully selects and cites clause variants, but the tested context shifts did not yet produce different chosen variant IDs for the non-NDA contract sets. That is a documented next-step improvement rather than something I would overclaim."

## Pending / Next Step

- Run and record a clean live-model `e2e` and `full` benchmark pass for the presentation appendix once the local long-running inference capture is stable.
- Add API key middleware to `app/api_server.py`, then unskip and run the auth test.
- Improve RAG context sensitivity for non-NDA clause families so context-shift tests can demonstrate actual variant changes, not just metadata completeness.
