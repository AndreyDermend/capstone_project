# LexiAgent — Deterministic Contract Drafting with AI Extraction

LexiAgent is a contract drafting system that combines **deterministic clause assembly** with **AI-powered field extraction** and **RAG-based clause selection**. Users describe the contract they need in plain English; LexiAgent extracts the fields, asks follow-up questions for anything missing, then assembles a complete contract from a governed clause library. Output is a professional DOCX with full source citations embedded in the document.

The app runs as an OpenAI-compatible API served by FastAPI. The user-facing interface is **Open WebUI**, connected to LexiAgent over a private Docker network.

---

## Architecture

```
User prompt (Open WebUI)
        |
        v
 LexiAgent API (FastAPI, /v1/chat/completions)
        |
        v
 AI extraction (Ollama: qwen3:4b) — evidence-gated
        |
        v
 Follow-up questions for missing fields
        |
        v
 RAG clause selection (Ollama: nomic-embed-text)
        |
        v
 Deterministic assembly (placeholder fill from vetted templates)
        |
        v
 DOCX contract + HTML artifact with hover tooltips
```

**Key design choices:**
- **No AI writes contract text.** All clause text comes from vetted legal templates (LawDepot, eForms, FormSwift, PandaDoc, OneNDA). The AI extracts fields and selects which variant to use — it never writes legal language.
- **Evidence-gated extraction.** Every extracted field must have quoted evidence from the user's input. No evidence ⇒ no field ⇒ follow-up question.
- **Conservative over creative.** The system prefers asking a follow-up over guessing. Scoring weights: 50% follow-up coverage, 30% extraction accuracy, 20% no-hallucination.

## Supported contract types

| Type | Subtypes | Clauses | Variants | Sources |
|------|----------|---------|----------|---------|
| Non-Disclosure Agreement | Mutual, Unilateral | 16 | ~3 each | LawDepot, eForms, FormSwift, PandaDoc, OneNDA |
| Consulting Agreement | Standard | 16 | 3 each | PandaDoc, LawDepot, eForms, FormSwift |
| Employment Agreement | Standard | 19 | 3 each | FormSwift, LawDepot, eForms |
| Service Agreement | Standard | 19 | 3 each | LawDepot, eForms, PandaDoc |

---

## Prerequisites

- **Docker** + **Docker Compose** v2
- **Ollama** running on the host (or containerized — see `docker-compose.yml`) with these models pulled:
  ```bash
  ollama pull qwen3:4b            # extraction model
  ollama pull nomic-embed-text    # embedding model for RAG
  ```

---

## Quick start

```bash
cp .env.example .env            # adjust OLLAMA_HOST if needed
docker compose up -d --build
curl http://localhost:8001/health
```

Then open **http://localhost:3000** (Open WebUI) and select **lexiagent** from the model dropdown.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | URL LexiAgent uses to reach Ollama |
| `EXTRACTION_MODEL` | `qwen3:4b` | Ollama model used for field extraction |
| `EMBED_MODEL` | `nomic-embed-text` | Ollama model used for RAG clause embeddings |
| `API_PORT` | `8001` | Host port exposed by the LexiAgent container |
| `LEXIAGENT_API_KEY` | *(empty)* | Optional bearer token for `/v1/*` routes |

---

## DOCX output

Each generated contract is a professional Word document:
- **Times New Roman** formatting with proper headings and numbered clauses
- **Blue highlighted** user-provided values with field markers (e.g. `[client_name]`)
- **Source citation** on every clause — template name, variant ID, RAG score
- **Audit appendix** at the end with two tables:
  - **Clause sources** — every clause's template, variant, selection method, similarity score
  - **User input evidence** — every extracted field with the exact text it was derived from

In the chat response, Open WebUI renders an interactive HTML artifact with hover tooltips over every clause and value (source template, RAG score, user evidence). The DOCX download link appears at the top of the assistant message.

---

## Demo prompts

### Service Agreement (full specification)
```
We need a service agreement between GlobalTech Solutions Inc. at 500 Market St,
San Francisco CA 94105 and CleanPro Services LLC at 200 Oak Blvd, Oakland CA 94612.
CleanPro will provide commercial office cleaning including daily janitorial, weekly
deep cleaning, and monthly floor maintenance for $3,500 per month, paid monthly.
Starting February 1, 2026. 30 days notice to terminate. California law, mediation
for disputes. Client owns all IP.
```
Extracts all 12+ fields and assembles immediately.

### NDA (minimal prompt → follow-ups)
```
I need an NDA between my company Apex Digital and a freelance designer we're
about to hire. We're based in New York.
```
Expected follow-ups: NDA type, addresses, emails, purpose, confidentiality period, dispute resolution.

### Consulting Agreement (almost no info → full questionnaire)
```
I need a consulting agreement for some data analytics work.
```
Expected: ~12 follow-up questions, zero hallucinated fields.

---

## Running the test suite

The repository ships with a layered test suite (API contract, follow-up parser, edge cases, RAG metadata, DOCX validation).

```bash
python3 app/test_api.py                         # API surface, no Ollama needed
python3 app/test_extraction.py followup         # follow-up parser
python3 app/test_extraction.py edge             # edge cases
python3 app/test_extraction.py rag all          # RAG metadata + selection
python3 app/test_extraction.py docx all         # DOCX validation
python3 app/test_extraction.py full             # everything
```

Tests write machine-readable reports to `output/test_results_*.json`. See `TESTING_METHODOLOGY_RESULTS.md` for the full methodology and last executed results.

---

## Project structure

```
Capstone_project/
  app/
    api_server.py              # OpenAI-compatible API for Open WebUI
    assemble_contract.py       # Deterministic contract assembler
    clause_rag.py              # RAG clause selection (nomic-embed-text)
    contract_docx.py           # DOCX generator with embedded citations
    contract_artifact.py       # HTML artifact with hover tooltips
    run_intake_loop.py         # Extraction pipeline (library, no CLI)
    test_api.py                # FastAPI endpoint tests
    test_extraction.py         # Extraction, RAG, edge, DOCX test suites
  config/
    contract_registry.json     # Maps contract types to their configs
    questionnaire_schema_*.json    # Field schemas per contract type
    assembly_order_*.json          # Clause ordering per contract type
    placeholder_mappings_*.json    # Field-to-placeholder maps
  data/
    clause_library/
      master_clause_library*.jsonl      # Governed clause texts
      master_clause_library*.npz        # Cached embeddings (auto-generated)
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env.example
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Connection refused` on Ollama | Start Ollama on the host (`ollama serve`) or uncomment an `ollama` service block in `docker-compose.yml`. |
| `host.docker.internal` not resolvable on Linux | The `extra_hosts: host.docker.internal:host-gateway` mapping in `docker-compose.yml` handles this. If Ollama binds to `127.0.0.1`, set `OLLAMA_HOST=0.0.0.0` in Ollama's systemd unit. |
| `qwen3:4b` not found | `ollama pull qwen3:4b && ollama pull nomic-embed-text` |
| Open WebUI can't see `lexiagent` | Confirm `OPENAI_API_BASE_URL=http://lexiagent:8001/v1` in the `open-webui` service (set by default in `docker-compose.yml`). |
| Changes not reflected | `docker compose up -d --build` to rebuild the LexiAgent image, then start a new chat in Open WebUI. |
| Extraction takes >2 minutes | Normal for `qwen3:4b` on CPU. Set `EXTRACTION_MODEL` to a smaller/remote model for throughput-sensitive deployments. |
