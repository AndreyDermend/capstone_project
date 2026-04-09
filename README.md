# LexiAgent - Deterministic Contract Drafting with AI Extraction

LexiAgent is a contract drafting system that combines **deterministic clause assembly** with **AI-powered field extraction** and **RAG-based clause selection**. Users describe what contract they need in plain English, and LexiAgent extracts the fields, asks follow-up questions for anything missing, then assembles a complete contract from a governed clause library with full source citations.

## Architecture

```
User prompt  -->  AI Extraction (Ollama qwen3:4b)
                      |
                      v
              Field Verification (evidence-gated)
                      |
                      v
              Follow-up Questions (for missing fields)
                      |
                      v
              RAG Clause Selection (nomic-embed-text)
                      |
                      v
              Deterministic Assembly (placeholder fill)
                      |
                      v
              Final Contract + Citations
```

**Key design choices:**
- **No AI writes contract text.** All clause text comes from vetted legal templates (LawDepot, eForms, FormSwift, PandaDoc). AI only extracts fields and selects which variant to use.
- **Evidence-gated extraction.** Every extracted field must have quoted evidence from the user's input. No evidence = no field = follow-up question instead.
- **Conservative over creative.** The system prefers asking a follow-up question over guessing. 50% weight on follow-up coverage, 30% on extraction accuracy, 20% on no-hallucination.

## Supported Contract Types

| Type | Subtypes | Clauses | Variants | Sources |
|------|----------|---------|----------|---------|
| Non-Disclosure Agreement | Mutual, Unilateral | 16 | ~3 each | LawDepot, eForms, FormSwift, PandaDoc |
| Consulting Agreement | Standard | 16 | 3 each | PandaDoc, LawDepot, eForms, FormSwift |
| Employment Agreement | Standard | 19 | 3 each | FormSwift, LawDepot, eForms |
| Service Agreement | Standard | 19 | 3 each | LawDepot, eForms, PandaDoc |

---

## Prerequisites

1. **Python 3.9+**
2. **Ollama** running locally with these models pulled:
   ```bash
   ollama pull qwen3:4b           # extraction model
   ollama pull nomic-embed-text    # embedding model for RAG
   ```
3. **Python packages:**
   ```bash
   pip3 install ollama numpy fastapi uvicorn gradio
   ```

---

## Quick Start (CLI)

Run the interactive intake loop directly:

```bash
python3 app/run_intake_loop.py
```

Select a contract type (1-4), describe what you need, answer follow-ups, and get your contract.

---

## Demo Setup: Gradio Web UI (Recommended)

The fastest way to demo LexiAgent. Works on Python 3.9+, no Docker required. Provides a ChatGPT-like interface.

### Step 1: Start Ollama

```bash
ollama serve
```

Verify it's running: `curl http://localhost:11434/api/tags`

### Step 2: Launch the Web UI

```bash
python3 app/web_ui.py
```

Open **http://localhost:7860** in your browser. That's it!

**Options:**
```bash
python3 app/web_ui.py --port 3000   # custom port
python3 app/web_ui.py --share        # public URL for remote demos
```

The `--share` flag generates a public Gradio URL (e.g., `https://xxxxx.gradio.live`) that anyone can access — useful for showing the demo remotely.

---

## Alternative: Open WebUI Integration

If you have Docker or Python 3.11+, you can use Open WebUI for a polished ChatGPT-like experience.

### Step 1: Start the LexiAgent API Server

```bash
python3 app/api_server.py
```

This exposes an OpenAI-compatible API at `http://localhost:8001/v1`.

### Step 2: Start Open WebUI

**Docker:**
```bash
docker run -d \
  -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

**pip (requires Python 3.11+):**
```bash
python3.11 -m pip install open-webui
python3.11 -m open_webui.main serve --port 3000
```

### Step 3: Connect LexiAgent to Open WebUI

1. Open **http://localhost:3000** and create an admin account
2. Go to **Admin Panel** > **Settings** > **Connections**
3. Under **OpenAI API**, click **+** to add a connection:
   - **URL**: `http://host.docker.internal:8001/v1` (Docker) or `http://localhost:8001/v1` (pip)
   - **API Key**: `sk-unused` (any non-empty string)
4. Click **Save**
5. Select **lexiagent** from the model dropdown and start chatting

---

## Demo Script: Example Prompts & Follow-ups

### Demo 1: Service Agreement (Full Specification)

Shows the system handling a complete request with no follow-ups needed.

**Prompt:**
```
We need a service agreement between GlobalTech Solutions Inc. at 500 Market St,
San Francisco CA 94105 and CleanPro Services LLC at 200 Oak Blvd, Oakland CA 94612.
CleanPro will provide commercial office cleaning including daily janitorial, weekly
deep cleaning, and monthly floor maintenance for $3,500 per month, paid monthly.
Starting February 1, 2026. 30 days notice to terminate. California law, mediation
for disputes. Client owns all IP.
```

**Expected behavior:** Extracts all 12+ fields, assembles immediately with RAG clause selection, shows full citations table.

---

### Demo 2: NDA with Follow-ups (Conversational)

Shows the follow-up question flow.

**Prompt:**
```
I need an NDA between my company Apex Digital and a freelance designer we're
about to hire. We're based in New York.
```

**Expected follow-ups:** The system will ask for:
- NDA type (Mutual or Unilateral)
- Your address
- The designer's name and address
- Email addresses for both parties
- Purpose of the NDA
- Confidentiality period
- Dispute resolution preference

**Example follow-up answer:**
```
1. Unilateral - we're the disclosing party
2. 100 Broadway, Suite 500, New York NY 10005
3. Maria Chen, 45 Park Ave, Brooklyn NY 11201
4. legal@apexdigital.com
5. maria@designstudio.com
6. Evaluating a website redesign project
7. 2 years
8. Arbitration
```

---

### Demo 3: Employment Agreement (Partial Info)

Shows extraction of what's available + targeted follow-ups for what's missing.

**Prompt:**
```
We're hiring a new marketing director at BrightPath Inc. Sarah Johnson will start
on March 1st at $145,000 per year. She'll work at our Denver office at 789 Pine St,
Denver CO 80202. Colorado law governs.
```

**Expected behavior:** Extracts employer name, employee name, job title, start date, salary, work location, governing law. Asks follow-ups for: employee address, job duties, employment basis (full-time/part-time), pay frequency, termination notice period.

**Example follow-up answer:**
```
1. 321 Elm St, Boulder CO 80301
2. Lead all marketing strategy, brand management, and digital campaigns
3. Full-Time
4. Bi-Weekly
5. 60 days
```

---

### Demo 4: Consulting Agreement (Minimal Prompt)

Shows how the system handles very little information gracefully.

**Prompt:**
```
I need a consulting agreement for some data analytics work.
```

**Expected behavior:** Extracts almost nothing (maybe just "data analytics" as services). Generates ~12 follow-up questions covering all required fields. No hallucination of names, addresses, or amounts.

**Example follow-up answer:**
```
1. Standard
2. Quantum Analytics Corp
3. 200 State St, Hartford CT 06103
4. analytics@quantumcorp.com
5. James Morrison Consulting
6. 55 Elm St, New Haven CT 06510
7. james@morrisonconsulting.com
8. January 15, 2026
9. Comprehensive data analytics services including pipeline design and dashboard creation
10. $15,000
11. Milestone-Based
12. June 30, 2026
13. 30
14. Connecticut
15. Client
```

---

### Demo 5: Quick Type Switching

Show that the same system handles all 4 contract types seamlessly. Try these back-to-back in separate conversations:

```
NDA between Acme Corp and Beta Labs for a potential acquisition discussion. Mutual. 3 years confidentiality. Delaware law.
```

```
Service contract for HVAC maintenance. CoolAir Systems will service our building at 100 Main St monthly for $1,200. Texas law. 15 days notice.
```

---

## What to Highlight in the Demo

1. **No AI-generated legal text** — Every word in the contract comes from vetted templates. AI only extracts fields and selects variants.

2. **Evidence-gated extraction** — Point out the "Extracted Fields" table. Every value has quoted evidence from the user's original text.

3. **Conservative follow-ups** — When information is missing, the system asks rather than guesses. Show this with Demo 2 or 4.

4. **RAG clause selection** — In the Citations table, point out the "RAG" method and similarity scores. The system picks the best-matching clause variant for the user's context.

5. **Source traceability** — Every clause cites its source template (LawDepot, eForms, PandaDoc, FormSwift). Full audit trail.

6. **Multi-contract support** — Same pipeline, same UI, 4 different contract types. The architecture is designed to scale to more.

---

## Running Tests

```bash
# Run all contract types
python3 app/test_extraction.py all

# Run specific type
python3 app/test_extraction.py nda
python3 app/test_extraction.py consulting
python3 app/test_extraction.py employment
python3 app/test_extraction.py service

# Run specific test ID
python3 app/test_extraction.py service 1
```

---

## Project Structure

```
Capstone_project/
  app/
    api_server.py              # OpenAI-compatible API for Open WebUI
    assemble_contract.py       # Deterministic contract assembler
    clause_rag.py              # RAG clause selection (nomic-embed-text)
    run_intake_loop.py         # AI extraction + follow-up pipeline
    test_extraction.py         # Extraction accuracy tests (16 tests)
  config/
    contract_registry.json     # Maps contract types to their configs
    questionnaire_schema_*.json    # Field schemas per contract type
    assembly_order_*.json          # Clause ordering per contract type
    placeholder_mappings_*.json    # Field-to-placeholder maps
  data/
    clause_library/
      master_clause_library*.jsonl      # Governed clause texts
      master_clause_library*.npz        # Cached embeddings
  output/                      # Generated contracts and test results
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'ollama'` | `pip3 install ollama` |
| `ModuleNotFoundError: No module named 'numpy'` | `pip3 install numpy` |
| `Connection refused` on Ollama | Run `ollama serve` first |
| `qwen3:4b` model not found | Run `ollama pull qwen3:4b` |
| Open WebUI can't find `lexiagent` model | Check the connection URL in Settings > Connections. Use `host.docker.internal` for Docker. |
| Extraction takes >2 minutes | Normal for qwen3:4b on CPU. GPU acceleration recommended for demos. |
| `LEXI_STATE` visible in chat | This is hidden state for conversation continuity. Rendering may vary by Open WebUI version. |
