#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StratoXFilter -- tkinter GUI (v3: X + Y + Z correction, per-axis backlash,
single-move MDI helper).

Per-axis enable checkboxes, optional per-axis map, per-axis two-value backlash
(0 = off), an automatic self-check, a single-move (MDI) calculator with two
modes, and an embedded Help window. No plotting (kept lean for a small .exe).
Bundled X map is the default.
"""

import os
import sys
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import stratofilter as sf
import mapbuilder as mb


APP_TITLE = "StratoXFilter"

# ---- colours (applied via the 'clam' ttk theme so they render on Windows) ---
C_BTN = "#3D5A80"        # secondary buttons
C_BTN_ACT = "#4E6E96"
C_BTN_FG = "#FFFFFF"
C_PRIM = "#E9A63B"       # primary action (Filter) -- matches the icon
C_PRIM_ACT = "#F2B857"
C_PRIM_FG = "#22272E"
C_ENTRY = "#F3E1B0"      # pale ochre -- input fields
C_OUT = "#DDEAD1"        # pale green -- computed (read-only) output fields
C_LOG = "#EEF1F4"        # neutral -- log area

HELP_TEXT = u"""StratoXFilter -- какво прави и как се използва
=================================================

НАЗНАЧЕНИЕ
Чете G-code (.TAP от ArtCAM за LinuxCNC) и записва коригиран .TAP за UCCNC.
Компенсира измерената позиционна грешка по X, Y и/или Z (посочно-зависимо),
плюс по избор луфт (backlash). Дъгите (G2/G3) се разбиват на къси прави
сегменти, защото коригирана дъга чупи UCCNC.

ФАЙЛОВА КОРЕКЦИЯ (горна част)
1. Input .TAP -> Browse. Output се предлага автоматично (_corr).
2. Чекни оста/осите за корекция (X / Y / Z, в произволна комбинация).
3. За всяка чекната ос: Map CSV (за X е вградената), анкер (X0/Y0/Z0),
   Backlash (две стойности, 0 = изкл).
   - Ос без карта, но чекната -> само backlash.
   - Некоригирана ос се копира едно към едно.
4. Filter -> записва коригирания файл + лог. Прави се и автоматична
   проверка (self-check); при проблем -> [ERROR] и предупреждение.

BACKLASH (магнитуди, положителни; знакът е по посоката; 0 = изкл)
  X: right = при +X, left = при -X
  Y: forward = при +Y, back = при -Y
  Z: up = при +Z, down = при -Z
Компенсирай луфта ИЛИ тук, ИЛИ в UCCNC -- никога и в двете.

ЕДИНИЧЕН ХОД (MDI helper, долна част)
За да намериш коригираната стойност за ръчно позициониране (напр. искаш
машината реално да отиде на X1000 -> въвеждаш From и To -> получаваш числото
за MDI).
- From = текуща позиция; To = желана цел. Посоката (From->To) избира кривата
  от картата и открива обръщане за backlash.
- Тикче "Sequential (+ backlash)":
    ВКЛ  -> добавя backlash; след Compute To се копира в From (за следващия ход).
    ИЗКЛ -> само геометрия, без backlash; From остава (пробвай няколко цели).
- Compute смята и трите оси наведнъж. From винаги е ръчно редактируем.
Използват се същите карти и backlash стойности от горните полета на всяка ос.

MAP BUILDER (бутон "Map builder...")
Прави карта (CSV) от измервателна таблица (.xlsx или .csv), за да не я
пишеш на ръка. Таблицата: една колона с header, съдържащ "commanded"
(командните позиции), плюс колони за пробезите -- заглавия с pos/right/
fwd/up (посока +) и neg/left/back/down (посока -). Произволен брой пасове
на посока; произволна стъпка и обхват. "Cell values are":
 - Measured positions: абсолютни позиции (същата рамка като commanded).
 - Raw scale readings (zeroed each pass): суровите показания на скалата,
   нулирана в началото на всеки пробег -- builder-ът смята measured =
   anchor + scale (anchor = старта: най-малкото commanded за +, най-голямото
   за -). Удобно за -посоката, когато не можеш да преднастроиш скалата.
 - Errors: вече сметнати грешки (measured - commanded).
Builder-ът взима МЕДИАНА по възел за всяка посока, correction = -error, и
записва CSV-то (по избор направо в ос X/Y/Z). READ ME таб в примера обяснява
всичко: measurments/measurement_template_EXAMPLE.xlsx.

ПАРАМЕТРИ НА ДЪГИТЕ
- Chord tol (mm): макс. отклонение на сегмент от дъгата (default 0.015).
- Min segment (mm): долна граница на сегмент (default 0.3).
- Dead-band (mm): реверс под тази стойност НЕ сменя посоката (default 0.3).

СМЯНА НА КАРТАТА
Посочи новия CSV в полето на оста. Формат:
commanded, error_pos, correction_pos, error_neg, correction_neg
(X: pos=надясно; Y: pos=напред; Z: pos=нагоре). Не е нужен нов build.

ТОЧНОСТ: ограничена от повторяемостта на машината (~0.05 mm).
"""


class App(object):
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.minsize(700, 700)
        self._init_style()

        self.in_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.sm_seq = tk.BooleanVar(value=False)   # single-move: sequential+backlash

        # per-axis configuration. pos = increasing coordinate, neg = decreasing.
        specs = [
            ("X", "Correct X", "X0", "right (+X)", "left (-X)", True, sf.default_map_path()),
            ("Y", "Correct Y", "Y0", "forward (+Y)", "back (-Y)", False, ""),
            ("Z", "Correct Z", "Z0", "up (+Z)", "down (-Z)", False, ""),
        ]
        self.axes = {}
        for name, title, alab, plab, nlab, en, mp in specs:
            self.axes[name] = {
                "title": title, "anchor_label": alab,
                "pos_label": plab, "neg_label": nlab,
                "enable": tk.BooleanVar(value=en),
                "map": tk.StringVar(value=mp),
                "anchor": tk.StringVar(value="0.0"),
                "bpos": tk.StringVar(value="0.0"),
                "bneg": tk.StringVar(value="0.0"),
                "sm_from": tk.StringVar(value="0.0"),
                "sm_to": tk.StringVar(value=""),
                "sm_corr": tk.StringVar(value=""),
                "sm_flank": None,
            }

        self.chord_var = tk.StringVar(value="0.015")
        self.minseg_var = tk.StringVar(value="0.3")
        self.dead_var = tk.StringVar(value="0.3")

        pad = {"padx": 6, "pady": 1}
        frm = ttk.Frame(root, padding=8)
        frm.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        # --- files ---
        files = ttk.Frame(frm)
        files.grid(row=0, column=0, sticky="ew")
        files.columnconfigure(1, weight=1)
        ttk.Label(files, text="Input .TAP:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(files, textvariable=self.in_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(files, text="Browse...", command=self.pick_input).grid(row=0, column=2, **pad)
        ttk.Label(files, text="Output .TAP:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(files, textvariable=self.out_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(files, text="Browse...", command=self.pick_output).grid(row=1, column=2, **pad)

        # --- axis groups ---
        r = 1
        for name in ("X", "Y", "Z"):
            self._axis_group(frm, r, name)
            r += 1

        # --- arc/params ---
        arcs = ttk.LabelFrame(frm, text="Arc / direction parameters", padding=5)
        arcs.grid(row=r, column=0, sticky="ew", pady=3); r += 1
        ttk.Label(arcs, text="Chord tol (mm):").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(arcs, textvariable=self.chord_var, width=8).grid(row=0, column=1, padx=4)
        ttk.Label(arcs, text="Min segment (mm):").grid(row=0, column=2, sticky="w", padx=4)
        ttk.Entry(arcs, textvariable=self.minseg_var, width=8).grid(row=0, column=3, padx=4)
        ttk.Label(arcs, text="Dead-band (mm):").grid(row=0, column=4, sticky="w", padx=4)
        ttk.Entry(arcs, textvariable=self.dead_var, width=8).grid(row=0, column=5, padx=4)

        # --- buttons ---
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, sticky="ew", pady=4); r += 1
        btns.columnconfigure(0, weight=1)
        self.run_btn = ttk.Button(btns, text="Filter  →  write corrected .TAP",
                                  style="Primary.TButton", command=self.run)
        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Single move...", command=self.show_single_move,
                   width=13).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(btns, text="Map builder...", command=self.show_map_builder,
                   width=13).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(btns, text="Help", command=self.show_help, width=7).grid(row=0, column=3)

        # --- log ---
        ttk.Label(frm, text="Log:").grid(row=r, column=0, sticky="w", padx=6); r += 1
        self.log = tk.Text(frm, height=5, wrap="word", state="disabled",
                           background=C_LOG, relief="flat", borderwidth=6)
        self.log.grid(row=r, column=0, sticky="nsew", padx=6, pady=(0, 6))
        frm.rowconfigure(r, weight=1)

        self._log("Ready. Default X map:\n  %s" % self.axes["X"]["map"].get())

    # ---- styling ----
    def _init_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TButton", background=C_BTN, foreground=C_BTN_FG,
                     borderwidth=1, focusthickness=0, padding=5)
        st.map("TButton",
               background=[("active", C_BTN_ACT), ("disabled", "#9AA6B2")],
               foreground=[("disabled", "#E6E6E6")])
        st.configure("Primary.TButton", background=C_PRIM, foreground=C_PRIM_FG,
                     padding=6, font=("TkDefaultFont", 9, "bold"))
        st.map("Primary.TButton", background=[("active", C_PRIM_ACT)])
        st.configure("TEntry", fieldbackground=C_ENTRY)
        st.map("TEntry", fieldbackground=[("readonly", C_ENTRY)])
        st.configure("Out.TEntry", fieldbackground=C_OUT)
        st.map("Out.TEntry", fieldbackground=[("readonly", C_OUT)])

    def _axis_group(self, parent, row, name):
        ax = self.axes[name]
        cb = ttk.Checkbutton(parent, text=ax["title"], variable=ax["enable"])
        grp = ttk.LabelFrame(parent, labelwidget=cb, padding=5)
        grp.grid(row=row, column=0, sticky="ew", pady=2)
        grp.columnconfigure(1, weight=1)
        ttk.Label(grp, text="Map CSV:").grid(row=0, column=0, sticky="w", padx=4, pady=1)
        ttk.Entry(grp, textvariable=ax["map"]).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(grp, text="Browse...",
                   command=lambda v=ax["map"]: self.pick_map(v)).grid(row=0, column=2, padx=4)
        ttk.Label(grp, text=ax["anchor_label"] + ":").grid(row=0, column=3, sticky="w", padx=4)
        ttk.Entry(grp, textvariable=ax["anchor"], width=8).grid(row=0, column=4, padx=4)

        bl = ttk.Frame(grp)
        bl.grid(row=1, column=0, columnspan=5, sticky="w", pady=(2, 0))
        ttk.Label(bl, text="Backlash:").grid(row=0, column=0, padx=4)
        ttk.Label(bl, text=ax["pos_label"]).grid(row=0, column=1, padx=(8, 2))
        ttk.Entry(bl, textvariable=ax["bpos"], width=8).grid(row=0, column=2)
        ttk.Label(bl, text=ax["neg_label"]).grid(row=0, column=3, padx=(12, 2))
        ttk.Entry(bl, textvariable=ax["bneg"], width=8).grid(row=0, column=4)

    def show_single_move(self):
        if getattr(self, "_sm_win", None) is not None and self._sm_win.winfo_exists():
            self._sm_win.lift()
            return
        win = tk.Toplevel(self.root)
        self._sm_win = win
        win.title("StratoXFilter -- Single move (MDI helper)")
        win.minsize(560, 300)
        grp = ttk.Frame(win, padding=10)
        grp.grid(row=0, column=0, sticky="nsew")
        win.rowconfigure(0, weight=1); win.columnconfigure(0, weight=1)
        for c in (2, 4, 6):
            grp.columnconfigure(c, weight=1)

        ttk.Label(grp, text="Enter From (current) and To (target); read the "
                  "corrected value to type in MDI.",
                  wraplength=520).grid(row=0, column=0, columnspan=7,
                                       sticky="w", pady=(0, 6))
        ttk.Checkbutton(grp, text="Sequential (+ backlash, auto-advance From)",
                        variable=self.sm_seq).grid(row=1, column=0, columnspan=7,
                                                   sticky="w", pady=(0, 6))
        ttk.Label(grp, text="From").grid(row=2, column=2)
        ttk.Label(grp, text="To").grid(row=2, column=4)
        ttk.Label(grp, text="Corrected").grid(row=2, column=6)
        rr = 3
        for name in ("X", "Y", "Z"):
            ax = self.axes[name]
            ttk.Label(grp, text=name + ":").grid(row=rr, column=0, padx=4, sticky="w")
            ttk.Entry(grp, textvariable=ax["sm_from"], width=12).grid(row=rr, column=2, padx=3, sticky="ew")
            ttk.Label(grp, text="→").grid(row=rr, column=3, padx=4)
            ttk.Entry(grp, textvariable=ax["sm_to"], width=12).grid(row=rr, column=4, padx=3, sticky="ew")
            ttk.Label(grp, text="=").grid(row=rr, column=5, padx=4)
            ttk.Entry(grp, textvariable=ax["sm_corr"], width=12, state="readonly",
                      style="Out.TEntry").grid(row=rr, column=6, padx=3, sticky="ew")
            rr += 1
        self._sm_status = ttk.Label(grp, text="", wraplength=520)
        self._sm_status.grid(row=rr, column=0, columnspan=7, sticky="w", pady=(6, 0)); rr += 1
        ttk.Button(grp, text="Compute", style="Primary.TButton",
                   command=self.compute_single).grid(row=rr, column=0, columnspan=5,
                                                      sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(grp, text="Close", command=win.destroy).grid(
            row=rr, column=5, columnspan=2, sticky="ew", pady=(8, 0))

    # ---- helpers ----
    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.update_idletasks()

    def pick_input(self):
        fn = filedialog.askopenfilename(
            title="Select input .TAP",
            filetypes=[("TAP files", "*.TAP *.tap *.nc *.ngc"), ("All files", "*.*")])
        if fn:
            self.in_var.set(fn)
            base, ext = os.path.splitext(fn)
            self.out_var.set(base + "_corr" + (ext or ".TAP"))

    def pick_output(self):
        fn = filedialog.asksaveasfilename(
            title="Save corrected .TAP as", defaultextension=".TAP",
            filetypes=[("TAP files", "*.TAP *.tap"), ("All files", "*.*")])
        if fn:
            self.out_var.set(fn)

    def pick_map(self, var):
        fn = filedialog.askopenfilename(
            title="Select error map CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if fn:
            var.set(fn)

    def _fnum(self, var, name):
        try:
            return float(var.get())
        except ValueError:
            raise ValueError("'%s' must be a number, got: %r" % (name, var.get()))

    def _load_axis_map(self, name, required_enabled):
        """Load an axis map from its field. If required_enabled is a BooleanVar
        that is False, returns None. If the field is empty, returns None
        (backlash-only). Raises on a missing named file."""
        ax = self.axes[name]
        if required_enabled and not ax["enable"].get():
            return None
        path = ax["map"].get().strip()
        if not path:
            return None
        if not os.path.isfile(path):
            raise IOError("%s map not found:\n%s" % (name, path))
        anchor = self._fnum(ax["anchor"], "%s anchor" % name)
        return sf.ErrorMap(path, anchor=anchor)

    def show_help(self):
        win = tk.Toplevel(self.root)
        win.title("StratoXFilter -- Help")
        win.minsize(600, 520)
        txt = tk.Text(win, wrap="word", padx=10, pady=10, background=C_LOG,
                      relief="flat")
        sb = ttk.Scrollbar(win, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).grid(
            row=1, column=0, columnspan=2, pady=6)

    # ---- map builder ----
    def show_map_builder(self):
        if getattr(self, "_mb_win", None) is not None and self._mb_win.winfo_exists():
            self._mb_win.lift()
            return
        win = tk.Toplevel(self.root)
        self._mb_win = win
        win.title("StratoXFilter -- Map builder")
        win.minsize(560, 260)
        f = ttk.Frame(win, padding=10)
        f.grid(row=0, column=0, sticky="nsew")
        win.rowconfigure(0, weight=1); win.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        self.mb_in = tk.StringVar()
        self.mb_out = tk.StringVar()
        self.mb_vals = tk.StringVar(value="Measured positions")
        self.mb_axis = tk.StringVar(value="(none)")

        ttk.Label(f, text="Build an error-map CSV from a measurement table "
                  "(.xlsx or .csv).", wraplength=520).grid(
                      row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(f, text="Table:").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(f, textvariable=self.mb_in).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(f, text="Browse...", command=self._mb_pick_in).grid(row=1, column=2, padx=4)
        ttk.Label(f, text="Output map CSV:").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(f, textvariable=self.mb_out).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(f, text="Browse...", command=self._mb_pick_out).grid(row=2, column=2, padx=4)

        opt = ttk.Frame(f)
        opt.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(opt, text="Cell values are:").grid(row=0, column=0, padx=4)
        ttk.Combobox(opt, textvariable=self.mb_vals, width=30, state="readonly",
                     values=["Measured positions",
                             "Raw scale readings (zeroed each pass)",
                             "Errors"]).grid(row=0, column=1, padx=4)
        ttk.Label(opt, text="Assign to axis:").grid(row=0, column=2, padx=(16, 4))
        ttk.Combobox(opt, textvariable=self.mb_axis, width=8, state="readonly",
                     values=["(none)", "X", "Y", "Z"]).grid(row=0, column=3, padx=4)

        self._mb_status = ttk.Label(f, text="", wraplength=520)
        self._mb_status.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(f, text="Generate map", style="Primary.TButton",
                   command=self._mb_generate).grid(row=5, column=0, columnspan=2,
                                                    sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(f, text="Close", command=win.destroy).grid(
            row=5, column=2, sticky="ew", pady=(8, 0))

    def _mb_pick_in(self):
        fn = filedialog.askopenfilename(
            title="Select measurement table",
            filetypes=[("Excel / CSV", "*.xlsx *.xlsm *.csv"), ("All files", "*.*")])
        if fn:
            self.mb_in.set(fn)
            base, _ = os.path.splitext(fn)
            if not self.mb_out.get().strip():
                self.mb_out.set(base + "_map.csv")

    def _mb_pick_out(self):
        fn = filedialog.asksaveasfilename(
            title="Save map CSV as", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if fn:
            self.mb_out.set(fn)

    def _mb_generate(self):
        try:
            inp = self.mb_in.get().strip()
            out = self.mb_out.get().strip()
            if not inp or not os.path.isfile(inp):
                messagebox.showwarning(APP_TITLE, "Choose a measurement table file.")
                return
            if not out:
                base, _ = os.path.splitext(inp)
                out = base + "_map.csv"
                self.mb_out.set(out)
            sel = self.mb_vals.get()
            if sel.startswith("Error"):
                va = "errors"
            elif sel.startswith("Raw"):
                va = "scale"
            else:
                va = "measured"
            n = mb.build_map_file(inp, out, values_are=va)
            # verify it loads
            sf.ErrorMap(out)
            msg = "Wrote %d nodes -> %s" % (n, os.path.basename(out))
            axis = self.mb_axis.get()
            if axis in ("X", "Y", "Z"):
                self.axes[axis]["map"].set(out)
                self.axes[axis]["enable"].set(True)
                msg += "  |  loaded into %s and ticked Correct %s" % (axis, axis)
            self._mb_status.configure(text=msg)
            self._log("Map builder: " + msg)
        except Exception as e:
            self._mb_status.configure(text="ERROR: %s" % e)
            self._log("Map builder ERROR: %s" % e)
            messagebox.showerror(APP_TITLE, "Map build failed:\n%s" % e)

    # ---- single move ----
    def compute_single(self):
        try:
            dead = self._fnum(self.dead_var, "Dead-band")
            seq = self.sm_seq.get()
            done = []
            for name in ("X", "Y", "Z"):
                ax = self.axes[name]
                to_s = ax["sm_to"].get().strip()
                if not to_s:
                    ax["sm_corr"].set("")
                    continue
                target = float(to_s)
                current = self._fnum(ax["sm_from"], "%s From" % name)
                emap = self._load_axis_map(name, required_enabled=False)
                bpos = self._fnum(ax["bpos"], "%s backlash +" % name)
                bneg = self._fnum(ax["bneg"], "%s backlash -" % name)
                corrected, flank = sf.single_move(
                    target, current, ax["sm_flank"], emap, bpos, bneg, seq, dead)
                ax["sm_corr"].set(sf.fmt(corrected))
                ax["sm_flank"] = flank
                if seq:                       # auto-advance for the next move
                    ax["sm_from"].set(to_s)
                done.append("%s: %s -> %s (%s%s)"
                            % (name, current, sf.fmt(corrected), flank,
                               ", +BL" if seq else ""))
            msg = (" | ".join(done) if done
                   else "Enter a To value for at least one axis.")
            self._log("Single move: " + msg)
            if getattr(self, "_sm_status", None) is not None:
                self._sm_status.configure(text=msg)
        except Exception as e:
            self._log("Single-move ERROR: %s" % e)
            if getattr(self, "_sm_status", None) is not None:
                self._sm_status.configure(text="ERROR: %s" % e)
            messagebox.showerror(APP_TITLE, "Single move failed:\n%s" % e)

    # ---- file filtering ----
    def run(self):
        try:
            inp = self.in_var.get().strip()
            out = self.out_var.get().strip()
            if not inp:
                messagebox.showwarning(APP_TITLE, "Choose an input .TAP file.")
                return
            if not os.path.isfile(inp):
                messagebox.showerror(APP_TITLE, "Input file not found:\n%s" % inp)
                return

            ex = self.axes["X"]["enable"].get()
            ey = self.axes["Y"]["enable"].get()
            ez = self.axes["Z"]["enable"].get()
            if not (ex or ey or ez):
                messagebox.showwarning(APP_TITLE, "Enable at least one axis.")
                return

            xmap = self._load_axis_map("X", required_enabled=True)
            ymap = self._load_axis_map("Y", required_enabled=True)
            zmap = self._load_axis_map("Z", required_enabled=True)
            xbr = self._fnum(self.axes["X"]["bpos"], "X backlash right")
            xbl = self._fnum(self.axes["X"]["bneg"], "X backlash left")
            ybf = self._fnum(self.axes["Y"]["bpos"], "Y backlash forward")
            ybb = self._fnum(self.axes["Y"]["bneg"], "Y backlash back")
            zbu = self._fnum(self.axes["Z"]["bpos"], "Z backlash up")
            zbd = self._fnum(self.axes["Z"]["bneg"], "Z backlash down")
            chord = self._fnum(self.chord_var, "Chord tol")
            minseg = self._fnum(self.minseg_var, "Min segment")
            dead = self._fnum(self.dead_var, "Dead-band")

            if not out:
                base, ext = os.path.splitext(inp)
                out = base + "_corr" + (ext or ".TAP")
                self.out_var.set(out)

            self.run_btn.configure(state="disabled")
            self._log("\n--- Filtering ---")
            self._log("in : %s" % inp)
            self._log("axes: X=%s Y=%s Z=%s | maps X=%s Y=%s Z=%s"
                      % (ex, ey, ez, bool(xmap), bool(ymap), bool(zmap)))

            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            result, stats = sf.process(
                lines, xmap=xmap, ymap=ymap, zmap=zmap,
                enable_x=ex, enable_y=ey, enable_z=ez,
                xb_right=xbr, xb_left=xbl, yb_fwd=ybf, yb_back=ybb,
                zb_up=zbu, zb_down=zbd,
                chord_tol=chord, min_seg=minseg, deadband=dead)
            with open(out, "w", encoding="utf-8", newline="\r\n") as fh:
                fh.write("\n".join(result) + "\n")

            self._log("out: %s" % out)
            self._log("lines %d -> %d | arcs %d -> %d seg | Xcorr=%d Ycorr=%d Zcorr=%d"
                      % (stats.lines_in, stats.lines_out, stats.arcs,
                         stats.arc_segments, stats.x_corrected,
                         stats.y_corrected, stats.z_corrected))

            checks = sf.self_check(
                lines, result, xmap, ymap, zmap, ex, ey, ez,
                xbr, xbl, ybf, ybb, zbu, zbd, chord, minseg)
            failed = False
            for sev, text in checks:
                self._log("[%s] %s" % (sev, text))
                if sev == "ERROR":
                    failed = True

            if failed:
                self._log("SELF-CHECK FAILED -- do not use the output.")
                messagebox.showerror(
                    APP_TITLE, "File written BUT self-check FAILED.\nSee the log "
                    "-- do not use the output until resolved.")
            else:
                self._log("DONE. Self-check passed.")
                messagebox.showinfo(
                    APP_TITLE, "Corrected file written and self-check passed:\n%s" % out)
        except Exception as e:
            self._log("ERROR: %s" % e)
            self._log(traceback.format_exc())
            messagebox.showerror(APP_TITLE, "Failed:\n%s" % e)
        finally:
            self.run_btn.configure(state="normal")


def main():
    if len(sys.argv) > 1:
        sf.main()
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
