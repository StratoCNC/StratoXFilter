#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StratoCNC G-code post-processor -- v2 (X + Y correction, per-axis backlash).

CAM (ArtCAM, LinuxCNC dialect) -> .TAP -> THIS FILTER -> corrected .TAP -> UCCNC

Per-axis, per-direction correction:
    X_out = X_in + Xmap(X, flankX) + backlashX(flankX)
    Y_out = Y_in + Ymap(Y, flankY) + backlashY(flankY)
  * Each axis is independently enabled (enable_x / enable_y).
  * Each axis map is OPTIONAL (map=None -> geometric term = 0). This allows
    backlash-only compensation on an axis with no geometric map.
  * Backlash values are POSITIVE magnitudes; the sign is applied by direction:
    moving in + direction adds +b_pos, moving in - direction adds -b_neg.
    A value of 0 means "off" for that direction. Per-direction (not
    accumulated) -> bounded, no drift over many reversals.
  * For X: flank "right" = +X (b_pos = XB_right), "left" = -X (b_neg = XB_left).
  * For Y: flank "right" = +Y = forward (b_pos = YB_forward),
           flank "left"  = -Y = backward (b_neg = YB_backward).
  * Z is NEVER corrected -- copied bit-for-bit.
  * Arcs (G2/G3) are split into G1 polylines (corrected arcs break UCCNC).
  * Absolute coords only (G90). Non-coordinate words (F, S, comments) preserved.

Python 3, stdlib only in the core. matplotlib/numpy only for --plot.
"""

import argparse
import bisect
import math
import os
import re
import sys


def default_map_path():
    """Path to the bundled X error-map CSV: inside the .exe (PyInstaller) or,
    from source, the repo copy under measurments/."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "X_axis_error_maps.csv")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "measurments", "X_axis_error_maps.csv")


# --------------------------------------------------------------------------
# 1. ErrorMap -- load CSV, build normalized, interpolating+extrapolating maps
# --------------------------------------------------------------------------
class ErrorMap:
    """Directional position-error map for ONE axis.

    CSV columns (same layout for X and Y maps):
        commanded_mm, error_pos_mm, correction_pos_mm, error_neg_mm, correction_neg_mm
      For X: pos = right (+X), neg = left (-X).
      For Y: pos = forward (+Y), neg = backward (-Y).
    We use the *correction* columns directly (correction = -error).

    Empty endpoint anchor cells (where the scale was zeroed) are filled with
    0.0 (correction = 0 there), NOT extrapolated.

    Outside the node range the map is linearly EXTRAPOLATED from the two end
    nodes. Each map is normalized so correction(anchor) == 0 for both flanks,
    so the filter never shifts the coordinate origin.
    """

    def __init__(self, csv_path, anchor=0.0):
        self.anchor = float(anchor)
        xs = []
        cp = []   # correction, positive direction (right / forward)
        cn = []   # correction, negative direction (left / backward)
        with open(csv_path, "r", encoding="utf-8-sig") as fh:
            fh.readline()  # skip header
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                v = float(parts[0])
                corr_p = parts[2].strip()
                corr_n = parts[4].strip()
                xs.append(v)
                cp.append(0.0 if corr_p == "" else float(corr_p))
                cn.append(0.0 if corr_n == "" else float(corr_n))

        # --- validate the commanded column: >=2 nodes, strictly ascending ---
        if len(xs) < 2:
            raise ValueError("map '%s' needs at least 2 nodes, got %d"
                             % (os.path.basename(csv_path), len(xs)))
        for k in range(1, len(xs)):
            if xs[k] <= xs[k - 1]:
                raise ValueError(
                    "map '%s': commanded column must be strictly ascending and "
                    "unique -- row %d (%.4f) is not greater than the previous "
                    "row (%.4f). Sort the rows and remove duplicates."
                    % (os.path.basename(csv_path), k + 2, xs[k], xs[k - 1]))

        self._xs = xs
        p0 = self._interp_extrap(self.anchor, xs, cp)
        n0 = self._interp_extrap(self.anchor, xs, cn)
        self._cp = [v - p0 for v in cp]
        self._cn = [v - n0 for v in cn]

    @staticmethod
    def _interp_extrap(x, xs, ys):
        """Linear interp inside the node range, linear extrapolation outside
        (using the first/last two nodes). Pure Python; xs strictly ascending."""
        if x <= xs[0]:
            if x == xs[0]:
                return float(ys[0])
            slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
            return float(ys[0] + slope * (x - xs[0]))
        if x >= xs[-1]:
            if x == xs[-1]:
                return float(ys[-1])
            slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
            return float(ys[-1] + slope * (x - xs[-1]))
        i = bisect.bisect_right(xs, x)
        x0, x1 = xs[i - 1], xs[i]
        y0, y1 = ys[i - 1], ys[i]
        return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))

    def correction(self, v, flank):
        """Geometric correction [mm] to ADD to the commanded coordinate."""
        if flank == "left":
            return self._interp_extrap(v, self._xs, self._cn)
        return self._interp_extrap(v, self._xs, self._cp)

    def max_abs_correction(self):
        """Largest |correction| over the nodes (both flanks) -- used as a
        sanity bound by the self-check."""
        m = 0.0
        for v in self._cp:
            m = max(m, abs(v))
        for v in self._cn:
            m = max(m, abs(v))
        return m


def load_map_optional(path, anchor=0.0):
    """Return an ErrorMap, or None if path is empty/missing."""
    if not path:
        return None
    if not os.path.isfile(path):
        raise IOError("map file not found: %s" % path)
    return ErrorMap(path, anchor=anchor)


def backlash_offset(flank, b_pos, b_neg):
    """Per-direction backlash offset (positive magnitudes, signed by direction).
    0 magnitude -> no offset for that direction."""
    if flank == "right":      # + direction
        return b_pos
    return -b_neg             # - direction


# --------------------------------------------------------------------------
# 4. DirectionSM -- flank state machine with dead-band (one per axis)
# --------------------------------------------------------------------------
class DirectionSM:
    """Tracks the active flank for one axis. Switches only on a genuine reversal
    larger than the dead-band; same-direction motion keeps the flank; sub
    dead-band moves (segments nearly parallel to the other axis) hold it."""

    def __init__(self, deadband=0.3, seed="right"):
        self.deadband = float(deadband)
        self.seed = seed
        self.flank = None

    def update(self, d):
        if self.flank is None:
            if d >= self.deadband:
                self.flank = "right"
            elif d <= -self.deadband:
                self.flank = "left"
            return self.flank if self.flank is not None else self.seed
        if self.flank == "right":
            if d <= -self.deadband:
                self.flank = "left"
        else:
            if d >= self.deadband:
                self.flank = "right"
        return self.flank


# --------------------------------------------------------------------------
# 2. Parser -- tokenize one line, keep modal state
# --------------------------------------------------------------------------
_WORD_RE = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")


def _split_code_comment(line):
    idx = line.find("(")
    if idx == -1:
        return line, False
    return line[:idx], True


def parse_words(line):
    code, _ = _split_code_comment(line)
    words = {}
    gcodes = []
    for m in _WORD_RE.finditer(code):
        letter = m.group(1).upper()
        val = float(m.group(2))
        if letter == "G":
            gcodes.append(int(round(val)))
        else:
            words[letter] = val
    return words, gcodes


# --------------------------------------------------------------------------
# 3. ArcExpander -- G2/G3 (I/J) -> list of (x, y, z) polyline points
# --------------------------------------------------------------------------
def expand_arc(x0, y0, z0, x1, y1, z1, i, j, cw, chord_tol, min_seg):
    """Expand an arc from (x0,y0) to (x1,y1), center at (x0+i, y0+j), into a
    list of end points [(x,y,z), ...] (start point NOT included). Z is linearly
    interpolated along the sweep (helix); planar arcs carry constant z."""
    cx = x0 + i
    cy = y0 + j
    r = math.hypot(x0 - cx, y0 - cy)
    if r < 1e-9:
        return [(x1, y1, z1)]

    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)

    if cw:
        sweep = a0 - a1
        while sweep <= 1e-12:
            sweep += 2.0 * math.pi
    else:
        sweep = a1 - a0
        while sweep <= 1e-12:
            sweep += 2.0 * math.pi

    if chord_tol < r:
        dtheta_tol = 2.0 * math.acos(max(-1.0, 1.0 - chord_tol / r))
    else:
        dtheta_tol = sweep
    dtheta_floor = min_seg / r
    dtheta = max(dtheta_tol, dtheta_floor)
    if dtheta <= 0:
        dtheta = sweep

    n = int(math.ceil(sweep / dtheta))
    if n < 1:
        n = 1

    pts = []
    for k in range(1, n + 1):
        frac = k / float(n)
        ang = a0 + (sweep if not cw else -sweep) * frac
        px = cx + r * math.cos(ang)
        py = cy + r * math.sin(ang)
        pz = z0 + (z1 - z0) * frac
        pts.append((px, py, pz))
    pts[-1] = (x1, y1, z1)
    return pts


# --------------------------------------------------------------------------
# 6. Emitter helpers
# --------------------------------------------------------------------------
def _err(msg):
    if sys.stderr is not None:
        sys.stderr.write(msg)


def fmt(v):
    return "%.4f" % v


def rewrite_token(raw_line, letter, new_val):
    """Replace only the <letter> number token in the code part of raw_line,
    keeping all other words, spacing and any trailing comment intact."""
    code, has_paren = _split_code_comment(raw_line)
    tail = raw_line[len(code):] if has_paren else ""
    new_code, n = re.subn(r"(?i)(" + letter + r")(-?\d*\.?\d+)",
                          lambda m: m.group(1) + fmt(new_val), code, count=1)
    if n == 0:
        return raw_line
    return new_code + tail


# --------------------------------------------------------------------------
# 5/7. Core processing pipeline
# --------------------------------------------------------------------------
class Stats(object):
    def __init__(self):
        self.lines_in = 0
        self.lines_out = 0
        self.arcs = 0
        self.arc_segments = 0
        self.x_corrected = 0
        self.y_corrected = 0
        self.z_corrected = 0


def process(lines, xmap=None, ymap=None, zmap=None,
            enable_x=True, enable_y=False, enable_z=False,
            xb_right=0.0, xb_left=0.0, yb_fwd=0.0, yb_back=0.0,
            zb_up=0.0, zb_down=0.0,
            chord_tol=0.015, min_seg=0.3, deadband=0.3):
    """Process raw input lines -> list of output lines. See module docstring."""
    x_sm = DirectionSM(deadband=deadband)
    y_sm = DirectionSM(deadband=deadband)
    z_sm = DirectionSM(deadband=deadband)
    stats = Stats()
    any_enabled = enable_x or enable_y or enable_z

    cx = cy = cz = 0.0
    have_pos = False
    motion = None
    out = []

    def corr_x(v, flank):
        c = 0.0
        if xmap is not None:
            c += xmap.correction(v, flank)
        c += backlash_offset(flank, xb_right, xb_left)
        return c

    def corr_y(v, flank):
        c = 0.0
        if ymap is not None:
            c += ymap.correction(v, flank)
        c += backlash_offset(flank, yb_fwd, yb_back)
        return c

    def corr_z(v, flank):
        c = 0.0
        if zmap is not None:
            c += zmap.correction(v, flank)
        c += backlash_offset(flank, zb_up, zb_down)
        return c

    for raw in lines:
        stats.lines_in += 1
        line = raw.rstrip("\n").rstrip("\r")
        words, gcodes = parse_words(line)
        for g in gcodes:
            if g in (0, 1, 2, 3):
                motion = g

        has_coord = any(k in words for k in ("X", "Y", "Z", "I", "J"))
        if not has_coord:
            out.append(line)
            stats.lines_out += 1
            continue

        nx = words.get("X", cx)
        ny = words.get("Y", cy)
        nz = words.get("Z", cz)
        is_arc = (motion in (2, 3)) and ("I" in words) and ("J" in words)

        # ---- arc: expand into corrected G1 polyline --------------------
        if is_arc and any_enabled:
            stats.arcs += 1
            cw = (motion == 2)
            pts = expand_arc(cx, cy, cz, nx, ny, nz,
                             words["I"], words["J"], cw, chord_tol, min_seg)
            fword = (" F" + fmt(words["F"])) if "F" in words else ""
            px, py, pz = cx, cy, cz
            helix = abs(nz - cz) > 1e-9
            for si, (sx, sy, sz) in enumerate(pts):
                fx = x_sm.update(sx - px)
                fy = y_sm.update(sy - py)
                fz = z_sm.update(sz - pz)
                ox = sx + (corr_x(sx, fx) if enable_x else 0.0)
                oy = sy + (corr_y(sy, fy) if enable_y else 0.0)
                seg = "G1 X" + fmt(ox) + " Y" + fmt(oy)
                if helix:
                    oz = sz + (corr_z(sz, fz) if enable_z else 0.0)
                    seg += " Z" + fmt(oz)
                    if enable_z:
                        stats.z_corrected += 1
                if si == 0 and fword:
                    seg += fword
                out.append(seg)
                stats.lines_out += 1
                stats.arc_segments += 1
                if enable_x:
                    stats.x_corrected += 1
                if enable_y:
                    stats.y_corrected += 1
                px, py, pz = sx, sy, sz
            cx, cy, cz = nx, ny, nz
            have_pos = True
            continue

        # ---- ordinary move (or arc with no axis enabled) ---------------
        fx = x_sm.update(nx - cx)
        fy = y_sm.update(ny - cy)
        fz = z_sm.update(nz - cz)
        newline = line
        if enable_x and "X" in words:
            newline = rewrite_token(newline, "X", nx + corr_x(nx, fx))
            stats.x_corrected += 1
        if enable_y and "Y" in words:
            newline = rewrite_token(newline, "Y", ny + corr_y(ny, fy))
            stats.y_corrected += 1
        if enable_z and "Z" in words:
            newline = rewrite_token(newline, "Z", nz + corr_z(nz, fz))
            stats.z_corrected += 1
        out.append(newline)
        stats.lines_out += 1

        cx, cy, cz = nx, ny, nz
        have_pos = True

    return out, stats


def single_move(target, current, last_flank, emap, b_pos, b_neg,
                use_backlash, deadband=0.3):
    """Correct a single commanded coordinate on one axis.

    Direction (flank) is taken from sign(target - current); moves smaller than
    the dead-band hold `last_flank`. The geometric map is applied in both modes;
    backlash is added only when use_backlash is True.

    Returns (corrected_value, flank_used)."""
    d = target - current
    if d > deadband:
        flank = "right"
    elif d < -deadband:
        flank = "left"
    else:
        flank = last_flank if last_flank else "right"
    corr = emap.correction(target, flank) if emap is not None else 0.0
    if use_backlash:
        corr += backlash_offset(flank, b_pos, b_neg)
    return target + corr, flank


# --------------------------------------------------------------------------
# 8. Automatic self-check -- independent verification, runs on every filter
# --------------------------------------------------------------------------
def _toolpath_points(lines, chord_tol, min_seg):
    """Reconstruct the ordered list of (x, y, z) motion points, expanding any
    arcs with the SAME chord/min_seg params. Lines without an X or Y word
    (e.g. plunge-only Z lines) update the modal position but yield no point,
    so an input file and its filtered output produce point lists that align
    1:1 (arcs expand to the same segment count both times)."""
    pts = []
    cx = cy = cz = 0.0
    motion = None
    for raw in lines:
        words, gcodes = parse_words(raw)
        for g in gcodes:
            if g in (0, 1, 2, 3):
                motion = g
        if not any(k in words for k in ("X", "Y", "Z", "I", "J")):
            continue
        nx = words.get("X", cx)
        ny = words.get("Y", cy)
        nz = words.get("Z", cz)
        if (motion in (2, 3)) and ("I" in words) and ("J" in words):
            for (sx, sy, sz) in expand_arc(cx, cy, cz, nx, ny, nz,
                                           words["I"], words["J"],
                                           motion == 2, chord_tol, min_seg):
                pts.append((sx, sy, sz))
        elif ("X" in words) or ("Y" in words) or ("Z" in words):
            pts.append((nx, ny, nz))
        cx, cy, cz = nx, ny, nz
    return pts


def self_check(in_lines, out_lines, xmap, ymap, zmap,
               enable_x, enable_y, enable_z,
               xb_right, xb_left, yb_fwd, yb_back, zb_up, zb_down,
               chord_tol, min_seg):
    """Verify invariants of a filter run. Returns a list of (severity, text)
    where severity is 'INFO', 'WARN' or 'ERROR'. Runs automatically."""
    msgs = []
    PT = 1e-3  # preservation tolerance (> 4-decimal rounding, < any real change)

    pb = _toolpath_points(in_lines, chord_tol, min_seg)
    pa = _toolpath_points(out_lines, chord_tol, min_seg)

    if len(pb) != len(pa):
        msgs.append(("ERROR", "structure changed: before=%d motion points, "
                     "after=%d (a move was dropped or added)"
                     % (len(pb), len(pa))))
        return msgs

    bound_x = (xmap.max_abs_correction() if xmap else 0.0) \
        + max(abs(xb_right), abs(xb_left)) + 1.0
    bound_y = (ymap.max_abs_correction() if ymap else 0.0) \
        + max(abs(yb_fwd), abs(yb_back)) + 1.0
    bound_z = (zmap.max_abs_correction() if zmap else 0.0) \
        + max(abs(zb_up), abs(zb_down)) + 1.0

    max_dx = max_dy = max_dz = 0.0
    z_frozen_bad = x_frozen_bad = y_frozen_bad = 0
    x_oob = y_oob = z_oob = 0
    first = {}
    for i in range(len(pb)):
        bx, by, bz = pb[i]
        ax, ay, az = pa[i]
        dx = abs(ax - bx); dy = abs(ay - by); dz = abs(az - bz)
        if dx > max_dx: max_dx = dx
        if dy > max_dy: max_dy = dy
        if dz > max_dz: max_dz = dz
        if not enable_x and dx > PT:
            x_frozen_bad += 1; first.setdefault("xf", (i, dx))
        if not enable_y and dy > PT:
            y_frozen_bad += 1; first.setdefault("yf", (i, dy))
        if not enable_z and dz > PT:
            z_frozen_bad += 1; first.setdefault("zf", (i, dz))
        if enable_x and dx > bound_x:
            x_oob += 1; first.setdefault("xo", (i, dx))
        if enable_y and dy > bound_y:
            y_oob += 1; first.setdefault("yo", (i, dy))
        if enable_z and dz > bound_z:
            z_oob += 1; first.setdefault("zo", (i, dz))

    if z_frozen_bad:
        i, d = first["zf"]
        msgs.append(("ERROR", "Z was modified on %d point(s) although Z "
                     "correction is OFF (first at #%d, dz=%.4f mm) -- Z must "
                     "be untouched" % (z_frozen_bad, i, d)))
    if x_frozen_bad:
        i, d = first["xf"]
        msgs.append(("ERROR", "X changed on %d point(s) although X correction "
                     "is OFF (first at #%d, dx=%.4f mm)" % (x_frozen_bad, i, d)))
    if y_frozen_bad:
        i, d = first["yf"]
        msgs.append(("ERROR", "Y changed on %d point(s) although Y correction "
                     "is OFF (first at #%d, dy=%.4f mm)" % (y_frozen_bad, i, d)))
    if x_oob:
        i, d = first["xo"]
        msgs.append(("ERROR", "X correction implausibly large on %d point(s) "
                     "(first at #%d, dx=%.3f > bound %.3f mm)"
                     % (x_oob, i, d, bound_x)))
    if y_oob:
        i, d = first["yo"]
        msgs.append(("ERROR", "Y correction implausibly large on %d point(s) "
                     "(first at #%d, dy=%.3f > bound %.3f mm)"
                     % (y_oob, i, d, bound_y)))
    if z_oob:
        i, d = first["zo"]
        msgs.append(("ERROR", "Z correction implausibly large on %d point(s) "
                     "(first at #%d, dz=%.3f > bound %.3f mm)"
                     % (z_oob, i, d, bound_z)))

    # independent chord-error measurement on the input arcs
    worst = 0.0
    cx = cy = cz = 0.0
    motion = None
    for raw in in_lines:
        words, gcodes = parse_words(raw)
        for g in gcodes:
            if g in (0, 1, 2, 3):
                motion = g
        if not any(k in words for k in ("X", "Y", "Z", "I", "J")):
            continue
        nx = words.get("X", cx); ny = words.get("Y", cy); nz = words.get("Z", cz)
        if (motion in (2, 3)) and ("I" in words) and ("J" in words):
            cxx = cx + words["I"]; cyy = cy + words["J"]
            r = math.hypot(cx - cxx, cy - cyy)
            prev = (cx, cy)
            for (sx, sy, sz) in expand_arc(cx, cy, cz, nx, ny, nz,
                                           words["I"], words["J"],
                                           motion == 2, chord_tol, min_seg):
                mx = (prev[0] + sx) / 2.0; my = (prev[1] + sy) / 2.0
                worst = max(worst, abs(math.hypot(mx - cxx, my - cyy) - r))
                prev = (sx, sy)
        cx, cy, cz = nx, ny, nz
    if worst > chord_tol * 1.05:
        msgs.append(("WARN", "arc chord error %.4f mm exceeds tolerance %.4f mm"
                     % (worst, chord_tol)))

    msgs.append(("INFO", "self-check: %d motion points | maxDX=%.4f maxDY=%.4f "
                 "maxDZ=%.4f | max chord=%.4f mm"
                 % (len(pb), max_dx, max_dy, max_dz, worst)))
    return msgs


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="StratoCNC X/Y correction TAP filter (v2)")
    ap.add_argument("input", help="input .TAP file")
    ap.add_argument("-o", "--output", help="output .TAP (default: <input>_corr.TAP)")
    ap.add_argument("--xmap", default=None, help="X error map CSV (default: bundled)")
    ap.add_argument("--ymap", default=None, help="Y error map CSV")
    ap.add_argument("--zmap", default=None, help="Z error map CSV")
    ap.add_argument("--no-x", action="store_true", help="disable X correction")
    ap.add_argument("--enable-y", action="store_true", help="enable Y correction (map optional)")
    ap.add_argument("--enable-z", action="store_true", help="enable Z correction (map optional)")
    ap.add_argument("--x0", type=float, default=0.0, help="X work-zero anchor (default 0)")
    ap.add_argument("--y0", type=float, default=0.0, help="Y work-zero anchor (default 0)")
    ap.add_argument("--z0", type=float, default=0.0, help="Z work-zero anchor (default 0)")
    ap.add_argument("--xb-right", type=float, default=0.0, help="X backlash, +X move")
    ap.add_argument("--xb-left", type=float, default=0.0, help="X backlash, -X move")
    ap.add_argument("--yb-fwd", type=float, default=0.0, help="Y backlash, +Y move")
    ap.add_argument("--yb-back", type=float, default=0.0, help="Y backlash, -Y move")
    ap.add_argument("--zb-up", type=float, default=0.0, help="Z backlash, +Z move")
    ap.add_argument("--zb-down", type=float, default=0.0, help="Z backlash, -Z move")
    ap.add_argument("--chord-tol", type=float, default=0.015, help="arc chord tolerance mm")
    ap.add_argument("--min-seg", type=float, default=0.3, help="min segment length mm")
    ap.add_argument("--deadband", type=float, default=0.3, help="direction dead-band mm")
    ap.add_argument("--plot", action="store_true", help="write before/after plot PNG")
    args = ap.parse_args(argv)

    enable_x = not args.no_x
    enable_y = args.enable_y or (args.ymap is not None)
    enable_z = args.enable_z or (args.zmap is not None)

    xmap_path = args.xmap if args.xmap is not None else default_map_path()
    xmap = load_map_optional(xmap_path if enable_x else None, anchor=args.x0)
    ymap = load_map_optional(args.ymap if enable_y else None, anchor=args.y0)
    zmap = load_map_optional(args.zmap if enable_z else None, anchor=args.z0)

    if not args.output:
        base, ext = os.path.splitext(args.input)
        args.output = base + "_corr" + (ext or ".TAP")

    with open(args.input, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    out, stats = process(lines, xmap=xmap, ymap=ymap, zmap=zmap,
                         enable_x=enable_x, enable_y=enable_y, enable_z=enable_z,
                         xb_right=args.xb_right, xb_left=args.xb_left,
                         yb_fwd=args.yb_fwd, yb_back=args.yb_back,
                         zb_up=args.zb_up, zb_down=args.zb_down,
                         chord_tol=args.chord_tol, min_seg=args.min_seg,
                         deadband=args.deadband)

    with open(args.output, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(out) + "\n")

    _err("in=%d out=%d lines | arcs=%d -> %d seg | Xcorr=%d Ycorr=%d Zcorr=%d\n"
         % (stats.lines_in, stats.lines_out, stats.arcs,
            stats.arc_segments, stats.x_corrected, stats.y_corrected,
            stats.z_corrected))
    _err("wrote %s\n" % args.output)

    # automatic self-check
    checks = self_check(lines, out, xmap, ymap, zmap,
                        enable_x, enable_y, enable_z,
                        args.xb_right, args.xb_left, args.yb_fwd, args.yb_back,
                        args.zb_up, args.zb_down, args.chord_tol, args.min_seg)
    for sev, text in checks:
        _err("[%s] %s\n" % (sev, text))
    if any(sev == "ERROR" for sev, _ in checks):
        _err("SELF-CHECK FAILED -- do not use the output.\n")

    if args.plot:
        make_plot(lines, out, xmap, ymap, args.input)


def make_plot(in_lines, out_lines, xmap, ymap, title):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def path_before(lines):
        xs, ys = [], []
        cx = cy = cz = 0.0
        motion = None
        for raw in lines:
            words, gcodes = parse_words(raw)
            for g in gcodes:
                if g in (0, 1, 2, 3):
                    motion = g
            if not any(k in words for k in ("X", "Y", "I", "J", "Z")):
                continue
            nx = words.get("X", cx); ny = words.get("Y", cy); nz = words.get("Z", cz)
            if motion in (2, 3) and "I" in words and "J" in words:
                for sx, sy, sz in expand_arc(cx, cy, cz, nx, ny, nz,
                                             words["I"], words["J"], motion == 2, 0.05, 0.3):
                    xs.append(sx); ys.append(sy)
            else:
                xs.append(nx); ys.append(ny)
            cx, cy, cz = nx, ny, nz
        return xs, ys

    def path_after(lines):
        xs, ys = [], []
        cx = cy = 0.0
        for raw in lines:
            words, gcodes = parse_words(raw)
            if "X" in words or "Y" in words:
                nx = words.get("X", cx); ny = words.get("Y", cy)
                xs.append(nx); ys.append(ny)
                cx, cy = nx, ny
        return xs, ys

    bx, by = path_before(in_lines)
    ax_, ay = path_after(out_lines)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 7))
    a1.plot(bx, by, "-", lw=0.5, color="tab:blue", label="before")
    a1.plot(ax_, ay, "-", lw=0.5, color="tab:red", label="after")
    a1.set_aspect("equal", "box")
    a1.set_title("Toolpath overlay: " + os.path.basename(title))
    a1.set_xlabel("X (mm)"); a1.set_ylabel("Y (mm)"); a1.legend()

    vs = np.linspace(0, 1520, 400)
    if xmap is not None:
        a2.plot(vs, [xmap.correction(v, "right") for v in vs], label="X right")
        a2.plot(vs, [xmap.correction(v, "left") for v in vs], label="X left")
    if ymap is not None:
        a2.plot(vs, [ymap.correction(v, "right") for v in vs], "--", label="Y fwd")
        a2.plot(vs, [ymap.correction(v, "left") for v in vs], "--", label="Y back")
    a2.axhline(0, color="k", lw=0.5)
    a2.set_title("Applied map correction")
    a2.set_xlabel("commanded (mm)"); a2.set_ylabel("correction (mm)"); a2.legend()

    png = os.path.splitext(title)[0] + "_plot.png"
    fig.tight_layout(); fig.savefig(png, dpi=110)
    _err("wrote %s\n" % png)


if __name__ == "__main__":
    main()
