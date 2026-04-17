"""
Self-contained HTML artifact generator for Open WebUI.

Renders the assembled contract as one HTML document with:
  - A subtle border + hover tooltip on every clause, showing its source
    template, variant, selection method, and RAG score.
  - A subtle border + hover tooltip on every user-provided value,
    showing the exact sentence from the user's input that was used.

The HTML is emitted as a ```html code block in the agent's chat response.
Open WebUI 0.3+ auto-renders that as a sandboxed artifact panel. The output
is fully self-contained (no external fetches) so nothing beyond what we
explicitly inline can be exposed.

Security (balanced tier):
  - Evidence sentences are truncated to ~120 chars
  - RAG scores are rounded to whole percent
  - No system prompts, model names, file paths, or losing candidate
    variants are exposed
  - The ``method`` field is surfaced honestly as either
    "RAG-selected (best of N)" or "Deterministic (only variant available)"
"""

import base64
import html as html_lib
import re
from typing import Any, Dict, List, Optional

EVIDENCE_MAX_LEN = 120
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
HEADING_NUMBERED_RE = re.compile(r"^\d+\.\s+[A-Z][A-Z\s/&\-]+$")
SUBHEADING_PREFIX_RE = re.compile(r"^(?:[A-Z]\.\s+|\([A-Za-z0-9]\)\s+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _truncate(s: str, max_len: int = EVIDENCE_MAX_LEN) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len].rstrip() + "\u2026"


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def _method_label(method: str, num_candidates: int) -> str:
    if method == "rag":
        return f"RAG-selected (best of {num_candidates} variants)"
    return "Deterministic (only variant available)"


def _is_all_caps_heading(line_text: str) -> bool:
    t = _strip_tags(line_text).strip()
    if len(t) < 3 or len(t) > 80:
        return False
    if t.startswith("{{") or t.startswith("_") or t.startswith("$"):
        return False
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _is_subheading(line_text: str) -> bool:
    t = _strip_tags(line_text).strip()
    if not SUBHEADING_PREFIX_RE.match(t):
        return False
    if len(t) > 60:
        return False
    body = SUBHEADING_PREFIX_RE.sub("", t).strip()
    if not body:
        return False
    if body.endswith(".") and " " in body:
        return False
    return True


# ---------------------------------------------------------------------------
# Placeholder wrapping
# ---------------------------------------------------------------------------
def _wrap_placeholders(
    raw_clause_text: str,
    inverse_mapping: Dict[str, str],
    answers: Dict[str, Any],
    evidence: Dict[str, str],
) -> str:
    """Replace ``{{TOKEN}}`` with a <span class="ph"> carrying the user's value
    plus its extraction evidence as data attributes. Non-placeholder text is
    html-escaped line-by-line in the caller; here we only escape the values
    we insert."""

    def _repl(match: "re.Match[str]") -> str:
        token = match.group(0)
        field = inverse_mapping.get(token)
        if field is None:
            return ""  # unknown placeholder — strip so it doesn't render
        value = str(answers.get(field, "")).strip()
        if not value:
            return ""
        ev = _truncate(str(evidence.get(field, "Provided directly in answer")))
        label = field.replace("_", " ").title()
        return (
            '<span class="ph" '
            f'data-field="{html_lib.escape(label, quote=True)}" '
            f'data-ev="{html_lib.escape(ev, quote=True)}">'
            f"{html_lib.escape(value)}"
            "</span>"
        )

    return PLACEHOLDER_RE.sub(_repl, raw_clause_text)


# ---------------------------------------------------------------------------
# Line-by-line HTML rendering (preserves placeholder spans)
# ---------------------------------------------------------------------------
def _escape_non_span_text(line: str) -> str:
    """Escape only the text between/around <span class="ph">...</span> tags.
    Everything outside the spans must be HTML-escaped; the spans themselves
    are already safe HTML."""
    parts = re.split(r'(<span class="ph"[^>]*>.*?</span>)', line)
    out = []
    for p in parts:
        if p.startswith('<span class="ph"'):
            out.append(p)
        else:
            out.append(html_lib.escape(p))
    return "".join(out)


def _render_clause_body(rendered_with_spans: str, is_first_clause: bool, title_already_emitted: bool) -> (str, bool):
    """Convert the (already placeholder-wrapped) clause text into HTML body
    markup. Returns (html, title_emitted_flag)."""
    out: List[str] = []
    title_emitted = title_already_emitted

    for raw in rendered_with_spans.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue

        text_only = _strip_tags(stripped).strip()

        if HEADING_NUMBERED_RE.match(text_only):
            out.append(f'<h2 class="h-numbered">{_escape_non_span_text(stripped)}</h2>')
            continue

        if _is_all_caps_heading(stripped):
            if is_first_clause and not title_emitted:
                out.append(f'<h1 class="contract-title">{_escape_non_span_text(stripped)}</h1>')
                title_emitted = True
            else:
                out.append(f'<h2 class="h-label">{_escape_non_span_text(stripped)}</h2>')
            continue

        if _is_subheading(stripped):
            out.append(f'<h3 class="h-sub">{_escape_non_span_text(stripped)}</h3>')
            continue

        out.append(f"<p>{_escape_non_span_text(stripped)}</p>")

    return "\n".join(out), title_emitted


# ---------------------------------------------------------------------------
# Inline CSS (self-contained)
# ---------------------------------------------------------------------------
CSS = """
:root {
  --fg: #1a1a1a;
  --muted: #5a6472;
  --border-subtle: rgba(80, 100, 140, 0.18);
  --border-hover: rgba(30, 80, 200, 0.55);
  --ph-border: rgba(0, 120, 80, 0.25);
  --ph-border-hover: rgba(0, 140, 90, 0.75);
  --tip-bg: #1f2430;
  --tip-fg: #f4f6fa;
  --bg: #ffffff;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: "Times New Roman", Georgia, serif;
  font-size: 16px;
  line-height: 1.55;
}
main {
  max-width: 780px;
  margin: 0 auto;
  padding: 40px 48px 80px;
}
.legend {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 12px;
  color: var(--muted);
  border-top: 1px solid #e5e8ee;
  border-bottom: 1px solid #e5e8ee;
  padding: 10px 0;
  margin-bottom: 24px;
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.legend b { color: #374151; font-weight: 600; }
.legend .swatch {
  display: inline-block;
  width: 14px; height: 14px;
  vertical-align: middle;
  margin-right: 6px;
  border-radius: 2px;
}
.legend .sw-clause { border: 1.5px solid var(--border-hover); }
.legend .sw-ph { border: 1.5px solid var(--ph-border-hover); }

h1.contract-title {
  font-size: 22px;
  text-align: center;
  margin: 0 0 24px;
  letter-spacing: 0.5px;
}
h2.h-numbered, h2.h-label {
  font-size: 15px;
  margin: 24px 0 10px;
  font-weight: bold;
}
h3.h-sub {
  font-size: 14px;
  margin: 14px 0 6px;
  font-weight: bold;
}
p {
  margin: 0 0 10px;
  text-align: justify;
}

.clause {
  position: relative;
  padding: 10px 14px;
  margin: 6px -14px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  transition: border-color 140ms ease, background-color 140ms ease;
}
.clause:hover {
  border-color: var(--border-hover);
  background: rgba(30, 80, 200, 0.025);
}
.clause > .c-tip {
  position: absolute;
  top: calc(100% + 4px);
  left: 12px;
  min-width: 260px;
  max-width: 360px;
  background: var(--tip-bg);
  color: var(--tip-fg);
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 12.5px;
  line-height: 1.45;
  padding: 10px 12px;
  border-radius: 6px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.18);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 120ms ease;
  z-index: 50;
}
.clause:hover > .c-tip {
  opacity: 1;
  visibility: visible;
}
.c-tip b { color: #a9c2ff; font-weight: 600; }
.c-tip .c-tip-row { margin: 2px 0; }

.ph {
  position: relative;
  display: inline;
  padding: 0 2px;
  margin: 0 -2px;
  border: 1px solid var(--ph-border);
  border-radius: 3px;
  background: rgba(0, 140, 90, 0.04);
  cursor: help;
  transition: border-color 120ms ease, background-color 120ms ease;
}
.ph:hover {
  border-color: var(--ph-border-hover);
  background: rgba(0, 140, 90, 0.09);
}
.ph::after {
  content: "\\201C" attr(data-ev) "\\201D  \\00B7  " attr(data-field);
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  min-width: 220px;
  max-width: 340px;
  padding: 8px 10px;
  background: var(--tip-bg);
  color: var(--tip-fg);
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 12px;
  line-height: 1.4;
  border-radius: 5px;
  box-shadow: 0 4px 14px rgba(0,0,0,0.18);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 120ms ease;
  white-space: normal;
  z-index: 60;
}
.ph:hover::after {
  opacity: 1;
  visibility: visible;
}
"""


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------
def generate_artifact_html(
    clauses: List[dict],
    rag_metadata: Optional[Dict[str, dict]],
    verified_answers: Dict[str, Any],
    evidence: Dict[str, str],
    placeholder_mappings: Dict[str, Dict[str, str]],
    subtype: str,
    contract_type: str,
    label: str,
    docx_bytes: Optional[bytes] = None,
    docx_filename: str = "contract.docx",
) -> str:
    """Build the self-contained HTML artifact string.

    ``placeholder_mappings`` is the full config dict (subtype -> {field: token});
    ``subtype`` selects the right sub-map.

    If ``docx_bytes`` is provided the DOCX is embedded as base64 and a
    download button is injected into the legend bar — no separate HTTP
    request or new-tab navigation required.
    """
    mapping = placeholder_mappings.get(subtype, {})
    inverse_mapping = {token: field for field, token in mapping.items()}

    clause_blocks: List[str] = []
    title_emitted = False

    for i, clause in enumerate(clauses):
        cname = clause.get("clause_name", "?")
        raw_text = clause.get("text", "")
        source = clause.get("source", "Unknown")
        variant = clause.get("variant_id", "?")

        meta = (rag_metadata or {}).get(cname, {})
        method = meta.get("method", "deterministic")
        num_cands = int(meta.get("num_candidates", 1) or 1)
        score = meta.get("score")
        score_pct = int(round(score * 100)) if isinstance(score, (int, float)) else None
        method_label = _method_label(method, num_cands)

        wrapped = _wrap_placeholders(raw_text, inverse_mapping, verified_answers, evidence)
        body_html, title_emitted = _render_clause_body(
            wrapped, is_first_clause=(i == 0), title_already_emitted=title_emitted
        )

        tip_rows = [
            f'<div class="c-tip-row"><b>Source:</b> {html_lib.escape(source)}</div>',
            f'<div class="c-tip-row"><b>Variant:</b> {html_lib.escape(str(variant))}</div>',
            f'<div class="c-tip-row"><b>Selection:</b> {html_lib.escape(method_label)}</div>',
        ]
        if method == "rag" and score_pct is not None:
            tip_rows.append(f'<div class="c-tip-row"><b>Match score:</b> {score_pct}%</div>')
        tip_html = "".join(tip_rows)

        clause_blocks.append(
            f'<section class="clause" aria-label="{html_lib.escape(cname)}">'
            f"{body_html}"
            f'<aside class="c-tip" role="note">{tip_html}</aside>'
            f"</section>"
        )

    legend = (
        '<div class="legend">'
        '<span><span class="swatch sw-clause"></span><b>Hover any clause</b>'
        " \u2014 shows source template &amp; selection method</span>"
        '<span><span class="swatch sw-ph"></span><b>Hover any highlighted value</b>'
        " \u2014 shows the phrase from your input it came from</span>"
        "</div>"
    )

    # Embed DOCX as base64 and inject a JS download button — eliminates the
    # separate /download HTTP request that caused multi-minute tab hangs.
    docx_script = ""
    dl_button = ""
    if docx_bytes:
        b64 = base64.b64encode(docx_bytes).decode("ascii")
        safe_filename = html_lib.escape(docx_filename, quote=True)
        docx_script = f"""<script>
function _dlDocx(){{
  var b64="{b64}";
  var raw=atob(b64);
  var buf=new Uint8Array(raw.length);
  for(var i=0;i<raw.length;i++)buf[i]=raw.charCodeAt(i);
  var blob=new Blob([buf],{{type:"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}});
  var a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download="{safe_filename}";
  document.body.appendChild(a);a.click();document.body.removeChild(a);
}}
</script>"""
        dl_button = '<button onclick="_dlDocx()" class="dl-btn">\u2b07\ufe0f Download DOCX</button>'

    header_title = html_lib.escape(label)

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{header_title}</title>
<style>{CSS}
.dl-btn{{
  font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:13px;font-weight:600;
  padding:6px 14px;border-radius:6px;border:none;
  background:#1d4ed8;color:#fff;cursor:pointer;
  margin-left:auto;white-space:nowrap;
}}
.dl-btn:hover{{background:#1e40af;}}
</style>
{docx_script}
</head>
<body>
<main>
{legend.replace('</div>', dl_button + '</div>', 1)}
{''.join(clause_blocks)}
</main>
</body>
</html>
"""
    return html_out
