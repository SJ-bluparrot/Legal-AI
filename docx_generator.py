"""
docx_generator.py — DOCX Document Generation Engine (Day 8)
-------------------------------------------------------------
Converts a generated complaint text string into a professionally formatted
Word document (.docx) that attorneys can download, edit, and file.

Architecture:
    Python (this module) parses the raw complaint text into a structured
    block list, serialises it to JSON, then invokes a Node.js child process
    that uses the 'docx' npm package to build the .docx binary.

    Python → parse → JSON → Node.js (docx) → .docx bytes → Python → HTTP

Why Node.js for DOCX generation:
    The 'docx' npm package produces standards-compliant .docx files with
    correct OOXML schema. python-docx is simpler but lacks reliable support
    for the exact formatting (tab stops, exact heading styles, section
    spacing) required by US court filing standards.

Block types produced by the parser:
    caption_line  — Court header / party lines (monospace, pre-formatted)
    header        — ALL-CAPS section title (bold, centered)
    subheader     — Mixed-case sub-section title (bold, left)
    numbered      — Numbered allegation paragraph
    lettered      — Lettered prayer-for-relief item (a. b. c.)
    body          — Regular prose paragraph
    spacer        — Intentional blank line

DOCX formatting follows US federal court filing conventions:
    - Times New Roman 12pt throughout
    - 1-inch margins (US Letter 8.5×11)
    - Double-spaced body text
    - Centered, bold section headers
    - Sequential numbered allegations
    - Hanging-indent numbered / lettered lists

Usage:
    from docx_generator import generate_complaint_docx

    docx_bytes = generate_complaint_docx(
        complaint_text = "IN THE UNITED STATES...",
        case_id        = "abc-123",
        case_type      = "personal_injury",
        attorney_name  = "Jane Smith",   # optional, defaults to [ATTORNEY NAME]
    )
    # docx_bytes is a bytes object — write to file or stream via FileResponse
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Node.js + docx package resolution
# ──────────────────────────────────────────────
# The docx npm package is expected to be installed either globally or in the
# project's node_modules. We try several common locations so this works in
# both dev and production environments.
_NODE_MODULES_CANDIDATES = [
    Path(__file__).parent / "node_modules",          # project-local (preferred)
    Path.home() / "node_modules",                    # user-local fallback
    Path("/usr/local/lib/node_modules"),             # global npm prefix
    Path("/usr/lib/node_modules"),                   # system npm prefix
]

def _find_node_modules() -> str:
    """Return the path to a node_modules dir that contains the docx package."""
    for candidate in _NODE_MODULES_CANDIDATES:
        if (candidate / "docx").exists():
            return str(candidate)
    raise RuntimeError(
        "The 'docx' npm package was not found. "
        "Install it with: npm install docx  (in the project directory)\n"
        f"Searched: {[str(c) for c in _NODE_MODULES_CANDIDATES]}"
    )


# ══════════════════════════════════════════════
# COMPLAINT TEXT PARSER
# ══════════════════════════════════════════════

_RE_NUMBERED   = re.compile(r'^(\d+)\.\s+(.+)$')
_RE_LETTERED   = re.compile(r'^([a-z])\.\s+(.+)$')
_RE_WHEREFORE  = re.compile(r'^WHEREFORE', re.IGNORECASE)


def _is_all_caps_header(line: str) -> bool:
    """
    Return True if a line qualifies as an ALL-CAPS section header.

    Rules:
      - At least 3 characters long after stripping
      - Every alphabetic character is uppercase
      - Not a numbered line (those start with digits)
      - Not a caption party line like "JOHN DOE,"
        (those are short and appear inside the caption block)
    """
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    if re.match(r'^\d', stripped):
        return False
    # Must contain at least one letter and all letters must be uppercase
    letters = [c for c in stripped if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _is_caption_line(line: str, in_caption: bool) -> bool:
    """
    Detect whether a line belongs to the formal court caption block.

    The caption is everything from the first line ("IN THE ...") through
    the first "Defendant." or "Respondent." line. Lines are flagged as
    caption lines while in_caption is True.
    """
    stripped = line.strip()
    return in_caption and bool(stripped)


def parse_complaint_text(text: str) -> dict:
    """
    Parse a complaint string into a list of typed blocks for DOCX formatting.

    Returns:
        {
            "caption_lines": [ "IN THE UNITED STATES DISTRICT COURT", ... ],
            "blocks": [
                {"type": "header",   "text": "COMPLAINT FOR NEGLIGENCE"},
                {"type": "body",     "text": "Plaintiff John Doe alleges..."},
                {"type": "numbered", "number": 1, "text": "Plaintiff..."},
                {"type": "lettered", "letter": "a", "text": "Compensatory..."},
                ...
            ]
        }

    The caption_lines list receives special monospace formatting in the DOCX.
    All other content goes into blocks, typed for paragraph-level formatting.
    """
    lines = text.splitlines()

    caption_lines: list[str] = []
    blocks: list[dict]       = []

    # Caption ends at the first line containing "Defendant(s)." or "Respondent(s)."
    _CAPTION_END = re.compile(r'(defendants?|respondents?)\.$', re.IGNORECASE)

    # Only enter caption mode when the text starts with a court header.
    # If Claude output the body directly (no caption), skip to body mode.
    _COURT_HEADER = re.compile(
        r'^(IN THE |SUPREME COURT|SUPERIOR COURT|UNITED STATES)', re.IGNORECASE
    )
    first_nonblank = next((l.strip() for l in lines if l.strip()), "")
    in_caption    = bool(_COURT_HEADER.match(first_nonblank))
    caption_ended = not in_caption

    i = 0
    while i < len(lines):
        raw  = lines[i]
        line = raw.strip()
        i   += 1

        # ── Empty line ─────────────────────────────────────────────────────────
        if not line:
            if not in_caption and blocks and blocks[-1]["type"] != "spacer":
                blocks.append({"type": "spacer"})
            continue

        # ── Caption accumulation ───────────────────────────────────────────────
        if in_caption and not caption_ended:
            caption_lines.append(raw.rstrip())   # preserve leading whitespace
            if _CAPTION_END.search(line):
                in_caption    = False
                caption_ended = True
            continue

        # ── Post-caption content ───────────────────────────────────────────────

        # Numbered allegation: "1. Plaintiff..." or "12. ..."
        m = _RE_NUMBERED.match(line)
        if m:
            blocks.append({
                "type":   "numbered",
                "number": int(m.group(1)),
                "text":   m.group(2).strip(),
            })
            continue

        # Lettered item: "a. Compensatory damages..."
        m = _RE_LETTERED.match(line)
        if m:
            blocks.append({
                "type":   "lettered",
                "letter": m.group(1),
                "text":   m.group(2).strip(),
            })
            continue

        # WHEREFORE / prayer opener — treat as body but flag for formatting
        if _RE_WHEREFORE.match(line):
            blocks.append({"type": "body", "text": line})
            continue

        # ALL-CAPS section header
        if _is_all_caps_header(line):
            # Remove consecutive spacers before a header
            while blocks and blocks[-1]["type"] == "spacer":
                blocks.pop()
            blocks.append({"type": "header", "text": line})
            continue

        # Signature block lines — everything after "Respectfully submitted"
        if line.lower().startswith("respectfully submitted"):
            blocks.append({"type": "subheader", "text": line})
            continue

        # Default: body paragraph
        blocks.append({"type": "body", "text": line})

    # Clean up trailing spacers
    while blocks and blocks[-1]["type"] == "spacer":
        blocks.pop()

    logger.debug(
        f"parse_complaint_text: {len(caption_lines)} caption lines, "
        f"{len(blocks)} blocks "
        f"({sum(1 for b in blocks if b['type']=='numbered')} numbered, "
        f"{sum(1 for b in blocks if b['type']=='header')} headers)"
    )

    return {"caption_lines": caption_lines, "blocks": blocks}


# ══════════════════════════════════════════════
# NODE.JS DOCX SCRIPT TEMPLATE
# ══════════════════════════════════════════════

_NODE_SCRIPT = r"""
'use strict';
const fs   = require('fs');
const path = require('path');

// Resolve docx from the node_modules path passed as env var
const nmPath  = process.env.NODE_MODULES_PATH;
const docxPkg = require(path.join(nmPath, 'docx'));

const {
  Document, Packer, Paragraph, TextRun,
  AlignmentType, HeadingLevel,
  LevelFormat, TabStopType, TabStopPosition,
  PageNumber, Footer, PageBreak,
  BorderStyle,
} = docxPkg;

// ── Input data from stdin ──────────────────────────────────────────────────
const input      = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const captionLines = input.caption_lines || [];
const blocks       = input.blocks        || [];
const meta         = input.meta          || {};

const outputPath = process.env.OUTPUT_PATH;

// ── Design constants ──────────────────────────────────────────────────────
const FONT        = 'Times New Roman';
const FONT_SIZE   = 24;    // half-points: 24 = 12pt
const LINE_SPACE  = 480;   // twips: 480 = double spacing (240 = single)
const PARA_AFTER  = 0;     // no extra space after paragraphs (double-spacing handles it)

// ── Helpers ───────────────────────────────────────────────────────────────
function run(text, opts = {}) {
  return new TextRun({
    text,
    font:  opts.font  || FONT,
    size:  opts.size  || FONT_SIZE,
    bold:  opts.bold  || false,
    underline: opts.underline ? {} : undefined,
  });
}

function para(children, opts = {}) {
  return new Paragraph({
    children: Array.isArray(children) ? children : [children],
    alignment: opts.align || AlignmentType.LEFT,
    spacing: {
      line:       opts.singleSpace ? 240 : LINE_SPACE,
      lineRule:   'auto',
      before:     opts.before !== undefined ? opts.before : 0,
      after:      opts.after  !== undefined ? opts.after  : PARA_AFTER,
    },
    numbering:  opts.numbering  || undefined,
    indent:     opts.indent     || undefined,
    border:     opts.border     || undefined,
  });
}

// ── Caption block ─────────────────────────────────────────────────────────
// Rendered in Courier New (monospace) at 11pt, single-spaced, left-aligned.
// This preserves the exact spatial layout of the formal court caption.
const captionParagraphs = captionLines.map(line => {
  const display = line.trimEnd();
  return para(
    [run(display || ' ', { font: 'Courier New', size: 22 })],
    { singleSpace: true, before: 0, after: 0 }
  );
});

// Add blank line after caption
captionParagraphs.push(para([run(' ')], { singleSpace: true, after: 0, before: 0 }));

// ── Content blocks ─────────────────────────────────────────────────────────
const contentParagraphs = [];

for (const block of blocks) {
  switch (block.type) {

    case 'spacer':
      // Spacers are implicit — double-spacing already provides paragraph separation.
      // Only emit a real spacer if we just had a header (to push body text down a bit).
      break;

    case 'header':
      contentParagraphs.push(
        para(
          [run(block.text, { bold: true, underline: true })],
          { align: AlignmentType.CENTER, before: 240, after: 120, singleSpace: true }
        )
      );
      break;

    case 'subheader':
      contentParagraphs.push(
        para(
          [run(block.text, { bold: true })],
          { align: AlignmentType.LEFT, before: 240, after: 120, singleSpace: true }
        )
      );
      break;

    case 'numbered':
      // Hanging indent: number at left margin, text indented 0.5 inch
      contentParagraphs.push(
        para(
          [run(`${block.number}.\t${block.text}`)],
          {
            indent: { left: 720, hanging: 360 },
            before: 0,
            after:  0,
          }
        )
      );
      break;

    case 'lettered':
      // Prayer-for-relief items — indented 0.5 inch with hanging indent
      contentParagraphs.push(
        para(
          [run(`${block.letter}.\t${block.text}`)],
          {
            indent: { left: 1080, hanging: 360 },
            before: 0,
            after:  0,
          }
        )
      );
      break;

    case 'body':
    default:
      contentParagraphs.push(
        para([run(block.text)], { before: 0, after: 0 })
      );
      break;
  }
}

// ── Footer: page number ────────────────────────────────────────────────────
const footer = new Footer({
  children: [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [
        new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 20 }),
        new TextRun({ text: ' of ', font: FONT, size: 20 }),
        new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 20 }),
      ],
    }),
  ],
});

// ── Assemble document ──────────────────────────────────────────────────────
const doc = new Document({
  styles: {
    default: {
      document: {
        run:       { font: FONT, size: FONT_SIZE },
        paragraph: {
          spacing: { line: LINE_SPACE, lineRule: 'auto', after: PARA_AFTER },
        },
      },
    },
  },
  sections: [{
    properties: {
      page: {
        size:   { width: 12240, height: 15840 },     // US Letter
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }, // 1-inch margins
      },
    },
    footers: { default: footer },
    children: [
      ...captionParagraphs,
      ...contentParagraphs,
    ],
  }],
});

// ── Write output ───────────────────────────────────────────────────────────
Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outputPath, buffer);
  process.exit(0);
}).catch(err => {
  process.stderr.write('DOCX generation error: ' + err.message + '\n');
  process.exit(1);
});
"""


# ══════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════

def generate_complaint_docx(
    complaint_text: str,
    case_id:        str,
    case_type:      str,
    attorney_name:  str = "",
) -> bytes:
    """
    Convert a complaint text string into DOCX bytes.

    Parses the complaint text, writes a structured JSON payload, runs a
    Node.js child process to produce the .docx binary, and returns the
    raw bytes suitable for streaming as a FileResponse.

    Args:
        complaint_text : Raw complaint string from complaint_drafter.draft_complaint()
        case_id        : Used for temp file naming and error messages
        case_type      : For logging / metadata only
        attorney_name  : Optional — injected into document metadata

    Returns:
        bytes — the raw .docx file content

    Raises:
        RuntimeError — if Node.js is unavailable, the docx package is missing,
                       or the Node process exits with a non-zero code.
    """
    node_modules = _find_node_modules()

    # ── Parse complaint into typed blocks ─────────────────────────────────────
    parsed = parse_complaint_text(complaint_text)
    parsed["meta"] = {
        "case_id":      case_id,
        "case_type":    case_type,
        "attorney":     attorney_name or "[ATTORNEY NAME]",
    }

    payload_json = json.dumps(parsed, ensure_ascii=False, indent=2)

    with tempfile.TemporaryDirectory(prefix=f"docx_{case_id[:8]}_") as tmp_dir:
        tmp_dir      = Path(tmp_dir)
        script_path  = tmp_dir / "generate.js"
        output_path  = tmp_dir / "complaint.docx"

        # Write the Node.js generation script
        script_path.write_text(_NODE_SCRIPT, encoding="utf-8")

        env = os.environ.copy()
        env["NODE_MODULES_PATH"] = node_modules
        env["OUTPUT_PATH"]       = str(output_path)

        try:
            result = subprocess.run(
                ["node", str(script_path)],
                input   = payload_json.encode("utf-8"),
                env     = env,
                capture_output = True,
                timeout = 30,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "Node.js is not installed or not on PATH. "
                "DOCX generation requires Node.js. "
                "Install from https://nodejs.org/"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"DOCX generation timed out after 30s for case_id={case_id}."
            )

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Node.js DOCX generation failed (exit {result.returncode}) "
                f"for case_id={case_id}.\nNode stderr: {stderr}"
            )

        if not output_path.exists():
            raise RuntimeError(
                f"Node.js exited 0 but no .docx file was created for case_id={case_id}."
            )

        docx_bytes = output_path.read_bytes()

    block_summary = {t: sum(1 for b in parsed["blocks"] if b.get("type") == t)
                     for t in ("header", "numbered", "lettered", "body")}
    logger.info(
        f"DOCX generated | case_id={case_id} | case_type={case_type} | "
        f"size={len(docx_bytes):,} bytes | blocks={block_summary}"
    )

    return docx_bytes