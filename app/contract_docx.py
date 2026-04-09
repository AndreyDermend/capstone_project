"""
DOCX contract generator with embedded citations.

Produces a professional Word document where:
- Each clause has a footnote citing the source template, variant, and RAG score
- Each user-provided value is highlighted and tracked via a "Placeholder Audit"
  appendix at the end of the document
- The document has proper formatting: title, headings, numbered clauses, signature lines
"""

import re
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from docx import Document
from docx.shared import Pt, Inches, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Footnote support (python-docx doesn't have built-in footnote API)
# ---------------------------------------------------------------------------
def _ensure_footnotes_part(doc):
    """Ensure the document has a footnotes part. Returns the footnotes element."""
    # Check if footnotes part already exists
    for rel in doc.part.rels.values():
        if "footnotes" in rel.reltype:
            return rel.target_part.element

    # Create footnotes part from scratch
    from docx.opc.part import Part
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    import copy

    footnotes_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:footnote w:type="separator" w:id="-1">'
        '<w:p><w:r><w:separator/></w:r></w:p>'
        '</w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0">'
        '<w:p><w:r><w:continuationSeparator/></w:r></w:p>'
        '</w:footnote>'
        '</w:footnotes>'
    )
    # For simplicity, we'll use a comment-based citation approach instead
    return None


def add_comment(doc, paragraph, text, author="LexiAgent", initials="LA"):
    """Add a comment annotation to a paragraph."""
    # Comments are complex in OOXML; use a simpler inline approach
    # We'll add the citation as a small italic run at the end of the paragraph
    run = paragraph.add_run(f"  [{text}]")
    run.font.size = Pt(7)
    run.font.color.rgb = RGBColor(128, 128, 128)
    run.font.italic = True


# ---------------------------------------------------------------------------
# Document styling
# ---------------------------------------------------------------------------
def setup_styles(doc: Document):
    """Configure document styles for a professional legal document."""
    # Default font
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Times New Roman"
    font.size = Pt(11)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.15

    # Title
    title_style = doc.styles["Title"]
    title_style.font.name = "Times New Roman"
    title_style.font.size = Pt(16)
    title_style.font.bold = True
    title_style.font.color.rgb = RGBColor(0, 0, 0)
    title_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_style.paragraph_format.space_after = Pt(12)

    # Heading 1 — clause headings
    h1 = doc.styles["Heading 1"]
    h1.font.name = "Times New Roman"
    h1.font.size = Pt(12)
    h1.font.bold = True
    h1.font.color.rgb = RGBColor(0, 0, 0)
    h1.paragraph_format.space_before = Pt(18)
    h1.paragraph_format.space_after = Pt(6)

    # Heading 2 — sub-headings
    h2 = doc.styles["Heading 2"]
    h2.font.name = "Times New Roman"
    h2.font.size = Pt(11)
    h2.font.bold = True
    h2.font.color.rgb = RGBColor(0, 0, 0)
    h2.paragraph_format.space_before = Pt(12)
    h2.paragraph_format.space_after = Pt(4)

    # Create citation style
    try:
        citation_style = doc.styles.add_style("Citation", WD_STYLE_TYPE.PARAGRAPH)
    except ValueError:
        citation_style = doc.styles["Citation"]
    citation_style.font.name = "Times New Roman"
    citation_style.font.size = Pt(7.5)
    citation_style.font.italic = True
    citation_style.font.color.rgb = RGBColor(100, 100, 100)
    citation_style.paragraph_format.space_before = Pt(0)
    citation_style.paragraph_format.space_after = Pt(8)

    # Section margins
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1.25)
        section.right_margin = Inches(1.25)


# ---------------------------------------------------------------------------
# Clause rendering
# ---------------------------------------------------------------------------
def render_clause_to_docx(
    doc: Document,
    clause: dict,
    clause_index: int,
    rag_meta: Optional[dict],
    replacements_used: Dict[str, str],
    evidence: Dict[str, str],
):
    """Render a single clause into the document with citations."""
    clause_name = clause.get("clause_name", "UNKNOWN")
    text = clause.get("text", "")
    source = clause.get("source", "Unknown")
    variant = clause.get("variant_id", "unknown")

    # Build citation string
    if rag_meta:
        method = rag_meta.get("method", "default").upper()
        score = rag_meta.get("score", 0)
        candidates = rag_meta.get("num_candidates", 1)
        citation = f"Source: {source} | Variant: {variant} | {method} selection ({candidates} candidates, {score:.0%} match)"
    else:
        citation = f"Source: {source} | Variant: {variant}"

    # Split text into lines for rendering
    lines = text.strip().split("\n")
    first_line = True

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect if this is a heading line (all caps, or starts with number + period + all caps)
        is_heading = (
            line == line.upper()
            and len(line) > 3
            and not line.startswith("$")
            and not line.startswith("_")
        )
        # Also detect "1. HEADING" pattern
        heading_match = re.match(r"^(\d+)\.\s+([A-Z][A-Z\s/&]+)$", line)

        if is_heading or heading_match:
            if heading_match:
                doc.add_heading(line, level=1)
            elif first_line and clause_index == 1:
                # First clause title — use document title
                doc.add_paragraph(line, style="Title")
            else:
                doc.add_heading(line, level=1)
            first_line = False
            continue

        # Sub-heading detection: "A. Something" or "(A) Something"
        sub_heading_match = re.match(r"^[A-Z]\.\s+", line) or re.match(r"^\([A-Z]\)\s+", line)

        if sub_heading_match:
            doc.add_heading(line, level=2)
        else:
            # Regular paragraph — add with placeholder highlighting
            para = doc.add_paragraph()
            # Split on placeholders that were replaced
            _render_paragraph_with_highlights(para, line, replacements_used, evidence)

        first_line = False

    # Add citation line after the clause
    citation_para = doc.add_paragraph(style="Citation")
    run = citation_para.add_run(f"[{citation}]")


def _render_paragraph_with_highlights(
    para,
    text: str,
    replacements_used: Dict[str, str],
    evidence: Dict[str, str],
):
    """Render paragraph text, highlighting user-provided values with evidence tooltips."""
    # Find all replaced values in the text and their positions
    segments = []
    remaining = text

    # Sort replacements by length (longest first) to avoid partial matches
    sorted_replacements = sorted(
        replacements_used.items(),
        key=lambda x: len(str(x[1])),
        reverse=True,
    )

    # Simple approach: scan for each replaced value
    highlight_ranges = []
    for field_name, value in sorted_replacements:
        val_str = str(value).strip()
        if not val_str or len(val_str) < 2:
            continue
        idx = text.find(val_str)
        if idx >= 0:
            ev = evidence.get(field_name, "User-provided")
            highlight_ranges.append((idx, idx + len(val_str), field_name, ev))

    # Sort by position
    highlight_ranges.sort(key=lambda x: x[0])

    # Remove overlapping ranges
    filtered = []
    last_end = 0
    for start, end, fname, ev in highlight_ranges:
        if start >= last_end:
            filtered.append((start, end, fname, ev))
            last_end = end

    # Render segments
    pos = 0
    for start, end, fname, ev in filtered:
        # Plain text before this highlight
        if pos < start:
            run = para.add_run(text[pos:start])

        # Highlighted value
        value_text = text[start:end]
        run = para.add_run(value_text)
        run.font.color.rgb = RGBColor(0, 51, 153)  # Dark blue
        run.bold = True

        # Add tiny superscript evidence marker
        sup_run = para.add_run(f"[{fname}]")
        sup_run.font.size = Pt(6)
        sup_run.font.color.rgb = RGBColor(140, 140, 140)
        sup_run.font.superscript = True

        pos = end

    # Remaining text
    if pos < len(text):
        run = para.add_run(text[pos:])


# ---------------------------------------------------------------------------
# Audit appendix
# ---------------------------------------------------------------------------
def add_audit_appendix(
    doc: Document,
    clauses: List[dict],
    rag_metadata: Optional[Dict[str, dict]],
    verified_answers: Dict[str, Any],
    evidence: Dict[str, str],
    contract_type: str,
):
    """Add an appendix with full audit trail: clause sources + placeholder evidence."""
    doc.add_page_break()
    doc.add_heading("APPENDIX: Document Audit Trail", level=1)

    disclaimer = doc.add_paragraph()
    disclaimer.style = doc.styles["Citation"]
    run = disclaimer.add_run(
        "This appendix is auto-generated by LexiAgent for traceability purposes. "
        "It documents the source of every clause and every user-provided value in this contract."
    )

    # --- Clause Sources ---
    doc.add_heading("A. Clause Sources", level=2)

    table = doc.add_table(rows=1, cols=5)
    table.style = "Light Grid Accent 1"
    headers = table.rows[0].cells
    headers[0].text = "#"
    headers[1].text = "Clause"
    headers[2].text = "Source Template"
    headers[3].text = "Selection Method"
    headers[4].text = "Score"

    for i, clause in enumerate(clauses, 1):
        cname = clause.get("clause_name", "?")
        source = clause.get("source", "Unknown")
        variant = clause.get("variant_id", "?")

        if rag_metadata and cname in rag_metadata:
            meta = rag_metadata[cname]
            method = f"{meta['method'].upper()} ({meta['num_candidates']} variants)"
            score = f"{meta['score']:.0%}"
        else:
            method = "Default"
            score = "N/A"

        row = table.add_row().cells
        row[0].text = str(i)
        row[1].text = cname
        row[2].text = f"{source} ({variant})"
        row[3].text = method
        row[4].text = score

    # Style table cells
    for row in table.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(9)

    # --- Placeholder Evidence ---
    doc.add_heading("B. User Input Evidence", level=2)

    doc.add_paragraph(
        "Each value below was extracted from the user's input. "
        "The \"Evidence\" column shows the exact text that was used as the basis for extraction.",
        style="Citation",
    )

    table2 = doc.add_table(rows=1, cols=3)
    table2.style = "Light Grid Accent 1"
    headers2 = table2.rows[0].cells
    headers2[0].text = "Field"
    headers2[1].text = "Extracted Value"
    headers2[2].text = "Source Evidence"

    for field_name, value in verified_answers.items():
        if not value or field_name == "special_provisions":
            continue
        ev = evidence.get(field_name, "User-provided (follow-up)")
        row = table2.add_row().cells
        row[0].text = field_name.replace("_", " ").title()
        row[1].text = str(value)
        row[2].text = str(ev)

    for row in table2.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(9)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
def generate_contract_docx(
    contract_text: str,
    clauses: List[dict],
    rag_metadata: Optional[Dict[str, dict]],
    verified_answers: Dict[str, Any],
    evidence: Dict[str, str],
    contract_type: str,
    label: str,
) -> Path:
    """
    Generate a professional DOCX contract with embedded citations.

    Returns the path to the generated file.
    """
    doc = Document()
    setup_styles(doc)

    # Build replacement map: field_name -> value (for highlighting)
    # We need to know which placeholders were replaced with what
    from assemble_contract import load_resources, build_replacements
    _, _, placeholder_mappings, config = load_resources(contract_type)
    subtype_field = config.get("subtype_field", "service_type")
    subtype = verified_answers.get(subtype_field, "Standard")
    mapping = placeholder_mappings.get(subtype, {})

    # Invert: field_name -> actual value used
    replacements_used = {}
    for field_name, placeholder in mapping.items():
        val = verified_answers.get(field_name, "")
        if val:
            replacements_used[field_name] = val

    # Render each clause
    for i, clause in enumerate(clauses, 1):
        cname = clause.get("clause_name", "")
        meta = rag_metadata.get(cname) if rag_metadata else None
        render_clause_to_docx(doc, clause, i, meta, replacements_used, evidence)

    # Add generation timestamp
    doc.add_paragraph()
    ts_para = doc.add_paragraph(style="Citation")
    ts_para.add_run(
        f"Generated by LexiAgent on {datetime.datetime.now().strftime('%B %d, %Y at %I:%M %p')} | "
        f"Contract Type: {label}"
    )

    # Add audit appendix
    add_audit_appendix(doc, clauses, rag_metadata, verified_answers, evidence, contract_type)

    # Save
    safe_type = contract_type.lower().replace(" ", "_")
    filename = f"{safe_type}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    output_path = OUTPUT_DIR / filename
    doc.save(str(output_path))

    return output_path


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Quick test with a service agreement
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from assemble_contract import assemble_contract

    answers = {
        "service_type": "Standard",
        "client_name": "GlobalTech Solutions Inc.",
        "client_address": "500 Market St, San Francisco, CA 94105",
        "contractor_name": "CleanPro Services LLC",
        "contractor_address": "200 Oak Blvd, Oakland, CA 94612",
        "services_description": "Commercial office cleaning including daily janitorial, weekly deep cleaning, and monthly floor maintenance.",
        "effective_date": "February 1, 2026",
        "compensation_amount": "$3,500 per month",
        "payment_schedule": "Monthly",
        "termination_notice_days": "30",
        "governing_law": "California",
        "ip_ownership": "Client",
        "special_provisions": "",
    }

    result = assemble_contract(answers, "ServiceAgreement", use_rag=True)
    contract_text, unresolved, clauses, rag_meta = (
        result if len(result) == 4 else (*result, None)
    )

    # Fake evidence for testing
    evidence = {k: f'User said: "{v}"' for k, v in answers.items() if v}

    path = generate_contract_docx(
        contract_text, clauses, rag_meta, answers, evidence,
        "ServiceAgreement", "Service Agreement",
    )
    print(f"Generated: {path}")
