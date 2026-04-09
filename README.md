# LexiAgent - Deterministic Contract Drafting with AI Extraction

LexiAgent is a contract drafting system that combines **deterministic clause assembly** with **AI-powered field extraction** and **RAG-based clause selection**. Users describe what contract they need in plain English, and LexiAgent extracts the fields, asks follow-up questions for anything missing, then assembles a complete contract from a governed clause library — output as a professional DOCX with full source citations embedded in the document.

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
              DOCX Contract + Embedded Citations
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
   pip3 install ollama numpy fastapi uvicorn gradio python-docx
   ```
4. **Docker Desktop** (for Open WebUI — optional but recommended for demo)

---

## Starting & Restarting the System

LexiAgent has 3 services. Each one runs in its own terminal.

### Full startup (first time or after reboot)

```bash
# Terminal 1: Ollama (AI models)
ollama serve

# Terminal 2: LexiAgent API server (connects your code to Open WebUI)
cd ~/Desktop/Capstone_project
python3 app/api_server.py

# Terminal 3: Open WebUI (Docker — only needed once, auto-restarts after)
docker start open-webui
```

Then open **http://localhost:3000** and select **lexiagent** from the model dropdown.

### After code changes — restart the API server

When you modify any Python file, you must restart the API server for changes to take effect. Open WebUI does NOT need to restart.

```bash
# In Terminal 2, press Ctrl+C to stop, then:
python3 app/api_server.py
```

Then **start a new chat** in Open WebUI (old conversations cache the previous behavior).

### Quick reference

| Service | How to start | How to restart | Persists? |
|---------|-------------|----------------|-----------|
| Ollama | `ollama serve` | Usually stays running, just leave it | Yes — models stay downloaded |
| LexiAgent API | `python3 app/api_server.py` | Ctrl+C then rerun | No — must restart after code changes |
| Open WebUI (Docker) | `docker start open-webui` | `docker restart open-webui` | **Yes** — account, settings, and chat history survive restarts and reboots |
| Docker Desktop | `open -a Docker` | Stays in menu bar | Yes — installed permanently |

---

## First-Time Docker + Open WebUI Setup

Only do this once. After setup, Open WebUI is permanently installed and accessible.

### Step 1: Install Docker Desktop

```bash
brew install --cask docker
```

Then launch it:
```bash
open -a Docker
```

Wait for the Docker whale icon in your menu bar to say "Docker Desktop is running" (~30 seconds on first launch). Docker Desktop stays installed permanently — you'll see it in your Applications folder and menu bar.

### Step 2: Install Open WebUI (one-time)

```bash
docker run -d \
  -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

This downloads and starts Open WebUI. The `--restart always` flag means it auto-starts whenever Docker Desktop is running. The `-v open-webui:/app/backend/data` flag stores your data in a Docker volume that survives container restarts, updates, and reboots.

### Step 3: Create your account

Open **http://localhost:3000** and create an admin account. This is stored locally — only you can access it.

### Step 4: Connect LexiAgent

1. Make sure the API server is running: `python3 app/api_server.py`
2. In Open WebUI, go to **Admin Panel** (gear icon, top-right) > **Settings** > **Connections**
3. Under **OpenAI API**, click **+** to add a connection:
   - **URL**: `http://host.docker.internal:8001/v1`
   - **API Key**: `sk-unused` (any non-empty string — our server doesn't check it)
4. Click the checkmark / **Save**
5. Go back to chat, click the **model dropdown** at the top — select **lexiagent**

This connection is saved permanently. You won't need to redo it.

### Step 5 (Optional): Make it a desktop app

**Safari:** Open `http://localhost:3000` > **File** > **Add to Dock** > Name it "LexiAgent"

**Chrome:** Open `http://localhost:3000` > **Three dots menu** > **Cast, save, and share** > **Install page as app**

---

## Everyday Usage (after first-time setup)

```bash
# 1. Make sure Docker Desktop is running (check menu bar for whale icon)
#    If not: open -a Docker

# 2. Open WebUI should auto-start with Docker. If not:
docker start open-webui

# 3. Start the LexiAgent API server
cd ~/Desktop/Capstone_project
python3 app/api_server.py

# 4. Open http://localhost:3000, select "lexiagent", start chatting
```

After code changes, just Ctrl+C the API server and rerun `python3 app/api_server.py`.

---

## Alternative: Gradio Web UI (no Docker needed)

If you don't want Docker, the Gradio UI works on Python 3.9+ with zero extra setup:

```bash
ollama serve                    # Terminal 1
python3 app/web_ui.py           # Terminal 2
# Open http://localhost:7860
```

For a public URL: `python3 app/web_ui.py --share`

---

## DOCX Output

The generated contract is a professional Word document with:
- **Times New Roman** formatting with proper headings and numbered clauses
- **Blue highlighted** user-provided values with field markers (e.g., `[client_name]`)
- **Source citation** on every clause — template name, variant ID, RAG score
- **Audit appendix** at the end with two tables:
  - **Clause Sources** — every clause's template, variant, selection method, and similarity score
  - **User Input Evidence** — every extracted field with the exact text it was derived from

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

**Expected behavior:** Extracts all 12+ fields, assembles immediately, outputs DOCX for download.

---

### Demo 2: NDA with Follow-ups (Conversational)

Shows the follow-up question flow.

**Prompt:**
```
I need an NDA between my company Apex Digital and a freelance designer we're
about to hire. We're based in New York.
```

**Expected follow-ups:** NDA type, addresses, emails, purpose, confidentiality period, dispute resolution.

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

**Prompt:**
```
We're hiring a new marketing director at BrightPath Inc. Sarah Johnson will start
on March 1st at $145,000 per year. She'll work at our Denver office at 789 Pine St,
Denver CO 80202. Colorado law governs.
```

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

**Prompt:**
```
I need a consulting agreement for some data analytics work.
```

**Expected behavior:** Extracts almost nothing. Generates ~12 follow-up questions. No hallucination.

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

## What to Highlight in the Demo

1. **No AI-generated legal text** — Every word in the contract comes from vetted templates.
2. **Evidence-gated extraction** — Every value has quoted evidence from the user's input.
3. **Conservative follow-ups** — Missing info = question, never a guess.
4. **RAG clause selection** — Open the DOCX audit appendix to show per-clause RAG scores.
5. **Source traceability** — Every clause cites its source template. Full audit trail in the document.
6. **Multi-contract support** — 4 contract types, same pipeline, same UI.
7. **Professional DOCX output** — Not raw text; a real document you could send to a lawyer.

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
    contract_docx.py           # DOCX generator with embedded citations
    run_intake_loop.py         # AI extraction + follow-up pipeline
    test_extraction.py         # Extraction accuracy tests (16 tests)
    web_ui.py                  # Gradio chat UI (alternative to Open WebUI)
  config/
    contract_registry.json     # Maps contract types to their configs
    questionnaire_schema_*.json    # Field schemas per contract type
    assembly_order_*.json          # Clause ordering per contract type
    placeholder_mappings_*.json    # Field-to-placeholder maps
  data/
    clause_library/
      master_clause_library*.jsonl      # Governed clause texts
      master_clause_library*.npz        # Cached embeddings (auto-generated)
  output/                      # Generated contracts and test results
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'ollama'` | `pip3 install ollama` |
| `ModuleNotFoundError: No module named 'numpy'` | `pip3 install numpy` |
| `ModuleNotFoundError: No module named 'docx'` | `pip3 install python-docx` |
| `Connection refused` on Ollama | Run `ollama serve` first |
| `qwen3:4b` model not found | Run `ollama pull qwen3:4b` |
| Open WebUI can't find `lexiagent` model | Check connection URL in Settings > Connections. Must be `http://host.docker.internal:8001/v1` |
| Changes not reflected in Open WebUI | Restart API server: Ctrl+C then `python3 app/api_server.py`. Start a **new chat**. |
| Docker not running | `open -a Docker` and wait for whale icon |
| Open WebUI container stopped | `docker start open-webui` |
| Extraction takes >2 minutes | Normal for qwen3:4b on CPU. GPU recommended. |
