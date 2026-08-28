# Cancelling the hidden nonlinear positioning error of a CNC machine — measure it, map it, correct the G-code

*How to diagnose the smooth, direction-dependent positioning error that
backlash compensation and steps-per-mm calibration can't fix — and a free tool
(StratoXFilter) that removes it by warping the G-code against a measured map.*

---

## 1. The problem nobody talks about

You calibrated steps-per-mm. You dialled in backlash compensation. Your homing
repeats beautifully. And yet a 800 mm feature comes out 799.8, an 1100 mm one
comes out 1101.2, and a bore that should be dead round is subtly egg-shaped.
The error isn't constant, it isn't proportional to distance, and it seems to
change depending on which way the axis was moving.

Welcome to **nonlinear, position-dependent positioning error** — the thing that
sits underneath backlash and scale error and quietly limits the accuracy of a
huge number of hobby and semi-pro machines, especially rack-and-pinion gantries.

It comes from real, physical, boring things:

- a rack that isn't perfectly straight or perfectly parallel to the linear rails
  (mesh depth drifts along travel, so the effective pitch radius drifts);
- pinion runout and a once-per-revolution ripple;
- gantry rail misalignment, frame flex, thermal drift;
- gearbox/reducer geometry.

The map of all this, along a 1.5 m axis, typically looks like a **smooth arch
plus a small periodic ripple** — on my machine, an arch peaking around
**+1.2 mm near the middle of travel**, with a **~0.07 mm ripple at the pinion's
once-per-turn period (~167 mm)** riding on top.

Three properties make it nasty:

1. **It's not linear.** Steps-per-mm (or servo E-gear) only fixes a constant
   scale factor. After you calibrate that out, the arch remains.
2. **It's not backlash.** Backlash is lost motion *at a reversal*. This error
   is present even while moving monotonically in one direction, and it varies
   with *position*, not with reversals.
3. **It's often direction-dependent.** Moving left-to-right and right-to-left,
   the machine traces a *different* error curve — on my machine the two curves
   differ by up to ~0.9 mm across the stroke.

Because of (1)–(3), the only thing that actually removes it is a **measured
map** of the error versus position, applied as a **direction-aware correction**
to the toolpath. That's what this article and the tool are about.

---

## 2. Diagnosis — is this really your problem?

A quick test tells you a lot. Command a long move one way, measure the real
travel; command it back, measure again.

On my machine, commanding ±800 mm gave:

| Direction | Commanded | Measured | Error |
|---|---|---|---|
| Right (+) | 800 mm | 799.85 mm | −0.15 mm |
| Left (−)  | 800 mm | 800.02 mm | +0.02 mm |

If it were **pure backlash**, both directions would show the *same* absolute
offset (backlash just shifts the zero reference). If it were a **pure scale
error**, both directions would be short (or long) by the *same* proportion.
Here the two directions differ in both magnitude and sign — the signature of a
**position-dependent, direction-dependent geometric error**, most likely a
rack/mesh issue, not backlash.

Two more confirmations:

- **Cut a test part and measure it.** If features are off by amounts that grow
  and shrink along the axis (not linearly), and the error depends on climb vs
  conventional direction of the pass, you have it.
- **Watch repeatability.** If repeated runs land within a few hundredths of a
  millimetre of each other, the error is *deterministic* — which means it can
  be mapped and cancelled. (If it's random, mapping won't help; fix the
  mechanics first.)

> Order of operations matters: **first** get steps-per-mm / servo E-gear right
> (removes the constant scale error), **then** measure the residual map. If you
> map before calibrating scale, the map just absorbs the scale error — harmless
> for cutting, but it hides what's going on.

---

## 3. Measuring the machine — the methodology

You need an independent length reference over the **full travel**. I used a
**1600 mm digital linear scale** so I could map the whole X axis without moving
the scale (moving it introduces stitching errors).

**Setup**
- Mount the scale rigidly, parallel to the axis. Bolt its read-head shuttle to
  the moving assembly (I used a bracket to the spindle carriage). Bracket
  stiffness matters — flex shows up as a fake direction-dependent offset.
- Deactivate the other axes so nothing else moves during a run.

**One pass (do this identically every time)**
1. **Fresh homing** to a repeatable datum (an inductive switch repeats to
   ~0.005 mm — plenty).
2. **Back off** a few mm in the direction you're about to map. This takes up
   the backlash *in that direction*, so the run is monotonic and backlash-free.
3. **Zero the scale.**
4. Command **monotonic incremental steps** across the whole travel — e.g.
   `G91 X20` repeated 76 times for 0–1520 mm. Record the scale reading at each
   node. Never reverse during a pass (a reversal injects backlash and ruins the
   run).
5. Repeat for the other direction (home, back off the *other* way, zero, step).

**The far end (for the reverse passes).** Most controllers home to one side
only — UCCNC has no built-in far-side homing. For the reverse direction you
still need a repeatable far-end datum. A short custom macro does it: jog into the
far limit sensor (finish in ~0.01 mm steps), read the scale at contact, then back
off by the amount that lands you on your chosen reference — e.g. the scale read
1530.11 mm at the sensor, so a 10.11 mm back-off sits exactly at 1520. Verify it
repeats before trusting it: re-run the macro a few times and check the scale
returns to the same value (mine held ±0.005 mm). The repeatability is what
matters, not the exact number — the filter normalizes each map, so a constant
offset in the datum cancels, but a datum that drifts pass-to-pass corrupts the
median.

**Robustness**
- Do **several passes per direction** (I did 5 each way) and take the
  **median** at each node — robust against the odd bad reading.
- Check pass-to-pass repeatability. Mine was ~0.05 mm peak-to-peak with 0.97+
  shape correlation between passes — i.e. the error is highly deterministic and
  worth cancelling. Your correction can only be as good as your repeatability;
  ~0.05 mm repeatability means ~0.05 mm is the accuracy floor, and chasing finer
  than that is pointless.

**Node spacing.** 20 mm captured both the broad arch and the pinion ripple on
my machine. Coarser (say 50 mm) still captures the arch fine but under-samples
the small ripple — usually fine, since the ripple is near the noise floor
anyway. The tool accepts any spacing (even non-uniform: dense where the curve
bends, sparse where it's straight), and each axis is independent.

**Keep backlash out of the map.** The monotonic, single-direction passes mean
the maps contain *geometry only*, no backlash. Backlash is a separate reversal
phenomenon and is compensated separately (see §6). Never mix the two — that
would double-count.

---

## 4. Building the maps

The tool reads one CSV per axis with this layout:

```
commanded_mm, error_pos_mm, correction_pos_mm, error_neg_mm, correction_neg_mm
```

- `pos` = the increasing-coordinate direction (X: right, Y: forward, Z: up);
  `neg` = the decreasing direction.
- `error` is (measured − commanded) at that node; `correction = −error`.
- The correction is applied as `commanded_out = commanded_in + correction`, with
  **linear interpolation** between nodes and **linear extrapolation** beyond the
  measured ends.

**Normalization (important).** The two directional curves are usually zeroed at
different physical points (I zeroed the right-going runs after a left home, and
the left-going runs after a right home). Their ends therefore don't meet at
zero. For cutting this is irrelevant — the G54 work offset absorbs any constant
shift — but the tool must not move your origin. So it **normalizes** each map so
that `correction(work_zero) = 0` for both directions, anchoring the correction
at your part zero. You just tell it the anchor (usually 0).

Requirements are minimal: at least 2 nodes, strictly ascending and unique
commanded values. The tool validates this on load and tells you exactly which
row is wrong if you fat-finger the CSV.

You rarely write this CSV by hand, though. A built-in **map builder** reads your
measurement spreadsheet (`.xlsx` or `.csv`) directly — no Excel or extra
libraries needed — computes `error = measured − commanded`, takes the median
across passes per node, and writes the map, optionally loading it straight into
an axis. Record your scale readings in the provided template (one column of
commanded positions, a few pass columns per direction), point the builder at it,
and click Generate. That closes the loop from "numbers in a spreadsheet" to
"map the filter can use" without any hand editing.

---

## 5. How the software solves it

Applying a directional correction to a toolpath sounds trivial — add the map
value to each coordinate — but three things need care.

**A. Arcs must be broken into short lines.** If you offset the start and end of
an arc by *different* amounts (which directional correction does), the arc's
radius no longer matches its endpoints and many controllers reject it or
distort it. The fix: replace each `G2/G3` with a **polyline of short `G1`
segments**, each segment corrected independently, so there's no radius to
mismatch. The tool splits by a **fixed chord tolerance** (default 0.015 mm — ten
times finer than machine repeatability), so tight arcs get many segments and
gentle arcs get few, with a minimum segment length (0.3 mm) so the controller's
look-ahead never chokes. The geometric error introduced by the polyline stays
below what the machine can even repeat.

**B. Picking the right directional curve.** The correction reads a different
curve depending on travel direction, chosen by the sign of the move's ΔX/ΔY/ΔZ.
But near features running parallel to another axis, ΔX jitters around zero and
would flip the curve back and forth. So a small **dead-band** (default 0.3 mm)
holds the active direction until a genuine reversal exceeds it — no chatter.

**C. Backlash (optional, separate).** If you *also* want to compensate backlash
in software (rather than in the controller), the tool adds a per-direction
offset — two values per axis (e.g. X right / X left) — on top of the geometric
map. It's bounded and doesn't drift over many reversals. **Compensate backlash
either here or in your controller, never both.** The geometric maps contain no
backlash (they're monotonic), so there's no double-counting with the map itself.

**D. Everything else is preserved.** Absolute mode only. Feeds, spindle speeds,
comments, the start/end blocks are copied verbatim. Z is bit-for-bit untouched
unless you enable Z correction. And every run ends with an **automatic
self-check** that independently verifies: untouched axes really didn't move,
corrections are within sane bounds, the arc chord tolerance actually holds, and
no moves were dropped or added. If anything looks wrong, it says so and tells
you not to use the output.

---

## 6. Using StratoXFilter

It's a single Windows executable (no install, no Python needed).

**File correction**
1. Pick the input `.TAP`; the output name auto-fills.
2. Tick the axis/axes to correct. For each: choose a map CSV (optional — an
   axis with no map but non-zero backlash does backlash-only), set the work-zero
   anchor, and the backlash values if you use them.
3. Hit **Filter**. It writes the corrected file and runs the self-check; the log
   shows exactly what happened.

**Single-move (MDI) helper.** Want to jog to a real X = 1000.000 by hand? Enter
`From` (current position) and `To` (target); the tool gives you the corrected
number to type into MDI (e.g. X1000.567). Two modes: *independent* (geometry
only, try several targets from one position) and *sequential* (adds backlash on
reversal and auto-advances `From` to `To` for the next move in a chain).

**Parameters** (sensible defaults, rarely touched): chord tolerance, minimum
segment length, direction dead-band, and per-axis work-zero anchors.

The maps are external CSVs — remeasure your machine, drop in new files, no
rebuild. That's what makes the tool machine- and CAM-agnostic.

---

## 7. Results and how to validate

Expect the residual error after correction to fall to roughly your machine's
**repeatability** — on mine, ~0.03–0.05 mm p95, down from a ~1.2 mm peak. You
can't beat repeatability with a static map, and you shouldn't try.

Validate in three cheap stages before trusting a real cut:

1. **On the bench (zero risk):** overlay the before/after toolpath (the tool can
   plot it) — the geometry should be preserved, shifted by only tenths of a mm,
   smoothly. Spot-check a coordinate by hand against the CSV. Confirm Z (and any
   disabled axis) is untouched. The built-in self-check does most of this
   automatically on every run.
2. **On the machine, no cutting:** command a corrected move to a nominal
   position and read it back with the *same* linear scale you mapped with. It
   should now land closer to nominal than the uncorrected command. This closes
   the loop with the same reference — the most direct proof.
3. **A real cut in cheap material (A/B):** cut a calibration piece (a grid of
   holes at known spacing, a long ruler, a big rectangle) once with the filter
   OFF and once ON, measure both against the drawing. The difference is your
   gain.

---

## 8. Scope and limits

- **Repeatability is the floor.** If your machine doesn't repeat, map correction
  won't save you — fix the mechanics first. This tool is for *deterministic*
  error.
- **Static map.** It doesn't adapt to load, temperature, or wear in real time.
  Remeasure occasionally.
- **Don't double-compensate.** Backlash goes either in the map's companion
  backlash fields *or* in your controller, never both. And don't add a linear
  correction if you've already fixed steps-per-mm / E-gear.
- **Verify before cutting.** It rewrites G-code; a bad map or wrong setting can
  move the tool. Dry-run first. (The self-check catches structural mistakes, not
  a wrong measurement.)

---

## 9. Download & license

**StratoXFilter** is free and open source under the **MIT license** — use it,
modify it, redistribute it; just keep the notice. No warranty; CNC is dangerous,
verify before you cut.

For a new machine you only need to do the measurement work in §3, build the maps
in §4, and load them. The correction engine, arc handling and backlash are
already generic across X, Y and Z, and independent of your CAM and controller.

*Questions, results, and improvements welcome — that's the point of releasing
it.*
