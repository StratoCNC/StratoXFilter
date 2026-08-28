# -*- coding: utf-8 -*-
"""
mapbuilder -- turn a measurement table (.xlsx or .csv) into an error-map CSV.

Reads a table of measured positions from repeated passes in both directions,
computes per-node error = measured - commanded, takes the MEDIAN across passes
(robust), and writes the 5-column map CSV that StratoXFilter consumes:

    commanded_mm, error_pos_mm, correction_pos_mm, error_neg_mm, correction_neg_mm

.xlsx is read with the standard library only (a .xlsx is a zip of XML), so no
third-party dependency is needed and the .exe stays small.

Table layout (flexible):
  - One header row somewhere in the first ~15 rows; a column header must
    contain "commanded" (that column holds the commanded positions).
  - Positive-direction pass columns: headers containing pos / right / fwd /
    forward / up / +   (X: right, Y: forward, Z: up).
  - Negative-direction pass columns: headers containing neg / left / back /
    down / -            (X: left, Y: back, Z: down).
  - Any number of pass columns per direction. Blank cells are ignored.
  - Cell values are MEASURED positions by default (error is computed here);
    pass values_are="errors" if the table already holds errors.
"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_POS_KEYS = ("pos", "right", "fwd", "forward", "up")
_NEG_KEYS = ("neg", "left", "back", "down")
_LABEL_MAX = 24   # header labels are short; prose sentences are longer


# --------------------------------------------------------------------------
# .xlsx reader (stdlib only)
# --------------------------------------------------------------------------
def _col_index(ref):
    m = re.match(r"[A-Za-z]+", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(0).upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _parse_sheet_xml(xml_bytes, shared):
    root = ET.fromstring(xml_bytes)
    data = root.find(_NS + "sheetData")
    rows = []
    if data is None:
        return rows
    for row in data.findall(_NS + "row"):
        cells = {}
        auto = 0
        for c in row.findall(_NS + "c"):
            ref = c.get("r")
            ci = _col_index(ref) if ref else auto
            auto = ci + 1
            t = c.get("t")
            v = c.find(_NS + "v")
            if t == "s":
                val = shared[int(v.text)] if (v is not None and v.text is not None) else None
            elif t == "inlineStr":
                isn = c.find(_NS + "is")
                val = "".join(x.text or "" for x in isn.iter(_NS + "t")) if isn is not None else None
            else:
                val = v.text if v is not None else None
            cells[ci] = val
        width = (max(cells) + 1) if cells else 0
        rows.append([cells.get(i) for i in range(width)])
    return rows


def _xlsx_sheets(path):
    """Return a list of grids, one per worksheet (workbook file order)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in ss.findall(_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        sheet_files = [n for n in names
                       if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]

        def _num(n):
            m = re.search(r"sheet(\d+)\.xml", n)
            return int(m.group(1)) if m else 0
        sheet_files.sort(key=_num)
        if not sheet_files:
            raise ValueError("no worksheet found in %s" % os.path.basename(path))
        return [_parse_sheet_xml(z.read(n), shared) for n in sheet_files]


def _read_xlsx(path):
    return _xlsx_sheets(path)[0]


def _read_csv(path):
    import csv
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        head = f.readline()
        delim = ";" if head.count(";") > head.count(",") else ","
        f.seek(0)
        return [r for r in csv.reader(f, delimiter=delim)]


def read_table(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    return _read_csv(path)


# --------------------------------------------------------------------------
# build the map
# --------------------------------------------------------------------------
def _tofloat(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    s = s.replace(",", ".")          # tolerate comma decimals
    try:
        return float(s)
    except ValueError:
        return None


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return s[n // 2]
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def build_map(grid, values_are="measured"):
    """grid: list of rows (list of cell values). Returns list of tuples
    (commanded, err_pos, corr_pos, err_neg, corr_neg); None where a direction
    has no data at that node."""
    def _pass_dir(h):
        # only treat SHORT cells as labels -> prose ("...measured position...")
        # never counts as a "pos" column.
        if not (0 < len(h) <= _LABEL_MAX):
            return None
        if any(k in h for k in _POS_KEYS):
            return "pos"
        if any(k in h for k in _NEG_KEYS):
            return "neg"
        return None

    def _is_cmd(h):
        return 0 < len(h) <= _LABEL_MAX and "command" in h

    # Pick the row that looks most like a header: has a 'commanded' label and
    # the most pass labels. Prose/instruction rows (one long cell) score 0.
    hdr = None
    best = 0
    for i, row in enumerate(grid[:25]):
        cells = [(str(c).strip().lower() if c is not None else "") for c in row]
        has_cmd = any(_is_cmd(c) for c in cells)
        passes = sum(1 for c in cells if _pass_dir(c))
        score = passes + (1 if has_cmd else 0)
        if has_cmd and passes >= 1 and score > best:
            best = score
            hdr = i
    if hdr is None:
        raise ValueError("Header row not found: need a row with a 'commanded' "
                         "column AND at least one pass column (pos/right/fwd/up "
                         "or neg/left/back/down).")
    header = [(str(c).strip().lower() if c is not None else "") for c in grid[hdr]]

    cmd_col = None
    pos_cols = []
    neg_cols = []
    for j, h in enumerate(header):
        if cmd_col is None and _is_cmd(h):
            cmd_col = j
            continue
        d = _pass_dir(h)
        if d == "pos":
            pos_cols.append(j)
        elif d == "neg":
            neg_cols.append(j)
    if cmd_col is None:
        raise ValueError("No 'commanded' column found.")
    if not pos_cols and not neg_cols:
        raise ValueError("No pass columns found. Header names must contain "
                         "pos/right/fwd/up (positive) or neg/left/back/down "
                         "(negative).")

    # collect the raw pass values per node
    records = []   # (commanded, [pos values...], [neg values...])
    for row in grid[hdr + 1:]:
        if cmd_col >= len(row):
            continue
        cmd = _tofloat(row[cmd_col])
        if cmd is None:
            continue
        pv = [v for v in (_tofloat(row[j]) for j in pos_cols if j < len(row)) if v is not None]
        nv = [v for v in (_tofloat(row[j]) for j in neg_cols if j < len(row)) if v is not None]
        records.append((cmd, pv, nv))

    # "scale" mode: cells are raw scale readings (scale zeroed at each pass's
    # start). measured_abs = anchor + scale, where the anchor is the pass start:
    # the + passes start at the LOWEST commanded, the - passes at the HIGHEST.
    pos_anchor = neg_anchor = None
    if values_are == "scale":
        pc = [c for c, pv, nv in records if pv]
        nc = [c for c, pv, nv in records if nv]
        pos_anchor = min(pc) if pc else None
        neg_anchor = max(nc) if nc else None

    def to_error(cmd, vals, anchor):
        if not vals:
            return None
        if values_are == "errors":
            return _median(vals)
        if values_are == "scale":
            if anchor is None:
                return None
            return _median([anchor + v - cmd for v in vals])
        return _median([v - cmd for v in vals])   # measured positions

    rows_out = []
    for cmd, pv, nv in records:
        ep = to_error(cmd, pv, pos_anchor)
        en = to_error(cmd, nv, neg_anchor)
        rows_out.append((cmd, ep, en))

    rows_out.sort(key=lambda t: t[0])
    result = []
    for cmd, ep, en in rows_out:
        cp = None if ep is None else -ep
        cn = None if en is None else -en
        result.append((cmd, ep, cp, en, cn))
    return result


def write_map_csv(path, rows):
    def s(x):
        return "" if x is None else ("%.4f" % x)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("commanded_mm,error_pos_mm,correction_pos_mm,"
                "error_neg_mm,correction_neg_mm\n")
        for cmd, ep, cp, en, cn in rows:
            f.write("%.4f,%s,%s,%s,%s\n" % (cmd, s(ep), s(cp), s(en), s(cn)))


def build_map_file(in_path, out_path, values_are="measured"):
    """Read a table file -> write a map CSV. Returns node count. For .xlsx the
    first worksheet that yields a valid measurement table is used, so extra
    sheets (e.g. a READ ME tab) are skipped automatically."""
    ext = os.path.splitext(in_path)[1].lower()
    grids = _xlsx_sheets(in_path) if ext in (".xlsx", ".xlsm") else [_read_csv(in_path)]
    rows = None
    last_err = None
    for grid in grids:
        try:
            r = build_map(grid, values_are=values_are)
            if len(r) >= 2:
                rows = r
                break
        except ValueError as e:
            last_err = e
    if rows is None:
        raise last_err or ValueError(
            "No usable measurement table found. Need a header row with a "
            "'commanded' column plus pass columns (pos/right/fwd/up or "
            "neg/left/back/down), and at least 2 data rows below it.")
    write_map_csv(out_path, rows)
    return len(rows)
