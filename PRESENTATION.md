---
title: "LexiAgent — Deterministic Contract Drafting"
subtitle: "Zero-hallucination contracts by confining AI to extraction"
author: "Andrey Dermen"
date: "April 21, 2026"
audience: "Company executives"
duration: "3–5 minutes of a 15-minute talk"
---

# Slide 1 — Architecture

## Headline
AI never writes a single word of the contract.

## Explanations (place under the diagram as three small captions)
- **Local Ollama** — runs a small model (qwen3:4b) on-device for field extraction. Privacy-first default; no data leaves the deployment.
- **Claude API (optional)** — opt-in swap for faster, more accurate extraction when throughput matters. Enabled with one environment variable.
- **Embeddings (always local)** — vector math for clause retrieval (nomic-embed-text). Stays on-device regardless of which extraction provider is active.

## Diagram specification for Claude PowerPoint
Render a three-stage flow, left-to-right. Rounded-rectangle boxes, thin stroke, subtle drop-shadow. Palette: muted navy primary (#1E3A5F), warm gold accent (#C9A961), off-white background (#F7F5F0). Connecting arrows thin, dark-grey, with small icons inline.

```
┌─────────────────┐    fields    ┌──────────────────────┐    variants    ┌──────────────────────┐
│  AI Extractor   │  ─────────►  │  Vetted Clause       │  ──────────►   │  Deterministic       │
│                 │              │  Library +           │                │  Assembler           │
│  Ollama (local) │              │  Embedding Index     │                │  (template + fill)   │
│  or Claude API  │              │  (always local)      │                │                      │
└─────────────────┘              └──────────────────────┘                └──────────────────────┘
                                                                                   │
                                                                                   ▼
                                                                          ┌─────────────────┐
                                                                          │ DOCX + HTML     │
                                                                          │ Artifact        │
                                                                          └─────────────────┘
```

Label above the left box: **"AI zone"** (shaded lightly). Label spanning the right two boxes: **"Deterministic zone — vetted legal text only"** (bolder shading).

## Works Cited (source attribution)
- **oneNDA** — open-standard NDA template (mutual + unilateral). https://www.onenda.org
- **LawDepot** — employment, consulting, and service agreement templates. https://www.lawdepot.com
- **eForms** — employment, consulting, and service templates. https://eforms.com
- **PandaDoc** — employment, consulting, and service templates. https://www.pandadoc.com
- **FormSwift** — employment and consulting templates. https://formswift.com

---

# Slide 2 — The Three Pillars

## Layout
Three equal-width boxes side-by-side. Same internal structure. Shared tradeoffs list underneath spanning full width.

## 🔒 Privacy & Security
1. **Self-contained deployment.** The API server, clause library, embeddings, and templates all run inside a single isolated environment (Docker network or Kubernetes namespace). The server is a closed box — nothing calls out except the optional Claude extraction provider.
2. **AI confined to structured extraction.** The model never writes contract text. Output is deterministically assembled from vetted legal templates — there is no path by which the model can inject language into the contract.
3. **Local-first by default.** Ollama runs on-device; Claude is opt-in and not required. User data never leaves the deployment boundary unless the operator explicitly enables the cloud provider.

## ✅ Reliability
1. **Evidence-gated extraction.** Every extracted value cites the user's own words. Hover any value in the artifact to see the exact source sentence.
2. **Conservative extraction.** A missing field becomes a follow-up question — never a guess. The system prefers three questions to one hallucination.
3. **Deterministic output.** Once fields are verified, template fill is reproducible byte-for-byte. Same inputs, same contract.

## 📈 Scalability
1. **Stateless compute tier.** Any pod can serve any request. Containers are interchangeable.
2. **Horizontal scale under Kubernetes.** Demonstrated with 4 parallel pods serving 4 different contract types in 9 seconds wall time.
3. **Provider-agnostic by design.** Swap Ollama ↔ Claude with one environment variable. No code changes, no retraining, no rebuild.

## Tradeoffs we made (and why) — full-width list below the three boxes
- **Privacy-first defaults cost speed.** Local inference is slower than cloud APIs. Operators who want throughput flip one env var — the code doesn't change.
- **Conservative extraction adds user turns.** More back-and-forth than a one-shot generator, but eliminates hallucination risk entirely. Follow-up coverage is the metric we optimize for, not first-try accuracy.
- **Supported contract types are limited to the vetted library.** Adding a new type requires curating templates. This is a deliberate quality gate, not a missing feature.
- **Multiple servers need to route you back to the same one.** When scaled out for speed, a user's follow-up answers must return to the server that handled the first message, otherwise it won't remember the conversation. In this demo we use a lightweight "stick the same user to the same server" rule. The production fix is a shared memory layer (Redis) so any server can pick up where another left off.

---

# Slide 3 — Test Coverage

## Headline
52 out of 52 tests passing. 99.7% average extraction score across 22 prompts.

## Formatted test table

| Category                  | What it validates                                           | Passed |
|---------------------------|-------------------------------------------------------------|:------:|
| Extraction accuracy       | Fields correctly pulled from natural prompts (4 contract types) | **22 / 22** |
| End-to-end pipeline       | Prompt → verified answers → DOCX + artifact, no unresolved placeholders | **4 / 4** |
| RAG clause selection      | Context-sensitive clause variants (e.g. arbitration vs litigation) | **4 / 4** |
| Edge cases                | Empty input, injection attempts, unicode, long input, contradictions, zero values | **6 / 6** |
| Follow-up parsing         | Numbered, labeled, and line-by-line answer formats          | **4 / 4** |
| DOCX validation           | File structure, section count, no placeholder leaks         | **4 / 4** |
| API surface               | `/health`, `/v1/*`, auth, streaming, sessions               | **8 / 9** † |
| **Total**                 |                                                             | **52 / 53** |

† *One API test is skipped pending optional bearer-token auth implementation — not a failure.*

## Per-contract-type extraction averages
- **NDA:** 100.0% (10 / 10)
- **Consulting:** 100.0% (4 / 4)
- **Employment:** 100.0% (4 / 4)
- **Service Agreement:** 98.9% (4 / 4, one near-empty prompt scored 95.5%)

## How extraction tests are scored (footnote / speaker note)
Each extraction test produces a composite score, weighted by what matters most for safety:
- **50% Follow-up coverage** — did the system ask about every missing required field?
- **30% Extraction accuracy** — of the fields extracted, how many matched expected values?
- **20% No-hallucination** — starts at 100% and subtracts 25% per invented field.

A test passes when its composite score ≥ 70%. Follow-up coverage is weighted highest because *asking when uncertain* is the safety behavior — the opposite of hallucination.

## Footnote
*Run against Claude (API) for extraction; local Ollama (`nomic-embed-text`) for embeddings. Same tests also run clean against local Ollama extraction (`qwen3:4b`).*

---

# Slide 4 — Codebase Structure

## Headline
Purpose-oriented layout. Each directory serves one role.

## Tree (rendered as a clean monospace block)

```
Capstone_project/
├── app/                  Extraction pipeline, RAG, assembly, DOCX rendering,
│                         API server, test suites
├── config/               Contract schemas — field definitions + assembly order
│                         per contract type
├── data/                 Clause library (text + vector embeddings) curated from
│                         vetted legal templates
├── k8s/                  Kubernetes manifests — horizontal scaling POC
├── Dockerfile            Containerized deployment image
└── docker-compose.yml    One-command local stack (LexiAgent + Open WebUI)
```

## Talking point
"The project structure tells the story: each folder maps to one role in the pipeline. No cross-cutting concerns, no hidden coupling."

---

# Slide 5 — Kubernetes in Action

## Headline
4 replicas. 4 different contract requests. 9 seconds wall time.

## Layout
Two screenshots side-by-side (large), captions below.

### Left screenshot
**File:** `assets/lens_pods.png` *(provided — Lens Pod view showing 4 `lexiagent-c844799d8-*` pods, namespace `lexiagent`, all Running, 0 restarts)*
**Caption:** "Four identical pods served four different contract-type requests concurrently."

### Right screenshot
**File:** `assets/docker_containers.png` *(provided — Docker Desktop Containers view showing `open-webui`, `lexiagent-control-plane` [kindest/node v1.35.0], `capstone_project-lexiagent` on port 8001)*
**Caption:** "Full stack running locally — UI, Kubernetes control plane, LexiAgent API — all in one isolated environment."

## Optional third element (small bar chart at the bottom)
```
Serial (estimated):   ████████████████████████████████  ~32s
Parallel (measured):  █████████                          9s
```

---

# Slide 6 — Demo (video, ~90 seconds)

## Headline
Three segments. Shown, not told.

## File
**Video:** `assets/demo.mp4` *(provided — will embed or link on the slide)*

## Segment breakdown (for speaker notes)

### 1. Uncovered contract type (~15s)
- **Prompt:** "I need a residential lease agreement for a property in Austin, Texas, landlord Maria Chen, tenant David Park, rent $2,400/month."
- **Expected:** system lists the 4 supported contract types and asks the user to choose — no hallucination, no invented lease.
- **Point:** *"It declines what it doesn't know."*

### 2. Covered type with follow-ups (~45s)
- **Prompt:** "I need a mutual NDA between Acme Corp (Delaware) and Beta LLC (California) to evaluate a potential partnership. Confidentiality period 3 years, New York governing law."
- **Follow-up answer:** "1. legal@acme.com  2. contracts@betallc.com  3. Arbitration"
- **Expected:** the system only asks for the 3 missing fields, then produces the DOCX + HTML artifact. Hovering over `legal@acme.com` in §8 Notices of the artifact shows a tooltip: "Value cited from: legal@acme.com"
- **Point:** *"It asks instead of guessing, and every value is traceable."*

### 3. Known limit — wrong conjugation (~20s)
- **Prompt:** "I need a mutual NDA between Acme Corp and Beta LLC to discuss a potential partnership. 3 years, New York law, arbitration. legal@acme.com and contracts@betallc.com."
- **Expected:** the rendered contract contains "in connection with **discuss a potential partnership**" — a verb phrase where the template expects a noun phrase.
- **Point:** *"The template concatenates user input verbatim. We surface limits honestly — grammar normalization is on the roadmap."*

## Closing line (if time permits)
"Every clause traceable. Every value with evidence. Every limit we already know about — on the roadmap."
