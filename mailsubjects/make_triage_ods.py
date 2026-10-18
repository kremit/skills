#!/usr/bin/env python3
"""Parse search_subject_simple.txt and emit a triage ODS spreadsheet."""

import re
import sys
from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import Style, TextProperties, TableColumnProperties, TableRowProperties
from odf.table import Table, TableColumn, TableRow, TableCell
from odf.text import P

INPUT  = sys.argv[1] if len(sys.argv) > 1 else "search_subject_simple.txt"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "triage.ods"

# ── Parse input ──────────────────────────────────────────────────────────────
rows = []          # (mode, category, term, count, notes)
current_mode     = ""
current_category = ""

MODE_HEADERS = {
    "# STARTS WITH": "starts-with",
    "# HAS WORDS":   "has-words",
    "# CONTAINS":    "contains",
}

with open(INPUT) as f:
    for raw in f:
        line = raw.rstrip()

        # Detect mode section headers (lines like "# STARTS WITH  (fastest...)")
        for prefix, mode in MODE_HEADERS.items():
            if line.startswith(prefix):
                current_mode = mode
                break

        # Detect category comment lines (e.g. "# --- Receipts: Orders ---")
        m = re.match(r"^#\s*[-–]+\s*(.+?)\s*[-–]*\s*$", line)
        if m:
            current_category = m.group(1)
            continue

        # Skip pure comment / blank / section header lines
        if not line or line.startswith("#"):
            continue

        # Parse a data line: "term    [~N]  # optional note"
        m = re.match(r"^(.+?)(?:\s+\[~?([\d+]+)\])?\s*(?:#\s*(.*))?$", line)
        if m:
            term  = m.group(1).strip()
            count = m.group(2) or ""
            note  = m.group(3) or ""
            if term:
                rows.append((current_mode, current_category, term, count, note))

# ── Build ODS ─────────────────────────────────────────────────────────────────
doc = OpenDocumentSpreadsheet()

# Styles
def make_style(doc, name, bold=False, bg=None, wrap=False):
    s = Style(name=name, family="table-cell")
    tp_attrs = {}
    if bold:
        tp_attrs["fontweight"] = "bold"
    if tp_attrs:
        s.addElement(TextProperties(**tp_attrs))
    if bg:
        from odf.style import TableCellProperties
        tcp_attrs = {"backgroundcolor": bg}
        if wrap:
            tcp_attrs["wrapoption"] = "wrap"
        s.addElement(TableCellProperties(**tcp_attrs))
    elif wrap:
        from odf.style import TableCellProperties
        s.addElement(TableCellProperties(wrapoption="wrap"))
    doc.styles.addElement(s)
    return name

hdr_style  = make_style(doc, "Header",  bold=True, bg="#C8D8E8")
mode_style = make_style(doc, "ModeRow", bold=True, bg="#E8E8E8")
plain      = make_style(doc, "Plain")
note_style = make_style(doc, "Note", wrap=True)

def cell(text, stylename=None):
    tc = TableCell(valuetype="string", stylename=stylename)
    tc.addElement(P(text=str(text) if text else ""))
    return tc

sheet = Table(name="Triage")
doc.spreadsheet.addElement(sheet)

# Column widths
for w in ["2.5cm", "4cm", "8cm", "1.5cm", "1cm", "5cm"]:
    col = TableColumn()
    sheet.addElement(col)

# Header row
hr = TableRow()
for h in ["Mode", "Category", "Search Term", "~Count", "Done", "Notes"]:
    hr.addElement(cell(h, hdr_style))
sheet.addElement(hr)

# Data rows — insert a shaded spacer row when mode changes
last_mode = None
for (mode, category, term, count, note) in rows:
    if mode != last_mode:
        # mode separator
        mr = TableRow()
        mc = TableCell(numbercolumnsspanned="6", valuetype="string",
                       stylename=mode_style)
        mc.addElement(P(text=f"── {mode.upper()} ──"))
        mr.addElement(mc)
        for _ in range(5):
            mr.addElement(TableCell())
        sheet.addElement(mr)
        last_mode = mode

    dr = TableRow()
    dr.addElement(cell(mode,     plain))
    dr.addElement(cell(category, plain))
    dr.addElement(cell(term,     plain))
    dr.addElement(cell(count,    plain))
    dr.addElement(cell("",       plain))   # Done — user checks off
    dr.addElement(cell(note,     note_style))
    sheet.addElement(dr)

doc.save(OUTPUT)
print(f"Wrote {len(rows)} rows to {OUTPUT}")
