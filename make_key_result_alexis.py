"""
make_key_result_alexis.py  --  the project's key quantitative result (HW10).

Estimated vs true magnetic-field tilt for every run in the pipeline cache, with the
pre-registered grade over true tilts of 30-60 deg (evaluate.grade: median |error| < 15 deg
AND slope > 0.5).  Reads only the committed cache, so it runs from a fresh clone:

    make result                    # = python data_pipeline_alexis.py --verify, then this script
    python make_key_result_alexis.py --db cache/pipeline_cache_alexis.db --out results

Writes
    results/tilt_recovery_alexis.png          the figure (low-S estimator, p_max = map maximum)
    results/tilt_recovery_grades_alexis.csv   grade per run x estimator x p_max rule
    results/tilt_recovery_grades_alexis.md    the same, as a Markdown table
    results/tilt_recovery_points_alexis.csv   every plotted point
and logs every step as JSON lines (results/key_result_run_alexis.jsonl when run by make).

Deterministic: no random numbers are drawn; rows are sorted; floats are rounded; the PNG
has no timestamp, and every rendering setting is pinned (font DejaVu Sans, which ships with
matplotlib; hinting; dpi), so local matplotlib defaults cannot change it.  Two runs give
byte-identical files; another environment reproduces the tables byte for byte and the
figure pixel for pixel (``make check-reproducible``; PNG bytes depend on the Pillow version).
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np                          # noqa: E402
import obs_logging_alexis as obs            # noqa: E402
from evaluate import ERR_LIMIT, SLOPE_MIN, TILT_MAX, TILT_MIN, grade_with_ci   # noqa: E402

SEEDS = obs.seed_everything(42)             # HW10: nothing here is random; set anyway

PRIMARY_RULE = "max"                        # pre-registered: p_max = map maximum
PRIMARY_EST = "gamma_obs_lowS"              # Chen et al. (2019) low-S estimator
ESTIMATORS = {"gamma_obs_lowS": "low-S", "gamma_obs": "all pixels"}
ORDERED_LIMIT = 1.0                         # same three-way rule as mcp_server_alexis.regime()
LOW_DISPERSION_LIMIT = 0.35
# fixed categorical order (validated palette, light mode); color follows the run, never its rank
RUN_STYLE = [  # (run_id prefix, label, color, marker)
    ("athenapp:", "Athena++ (own run)", "#257de0", "o"),
    ("cho:b1p1:", "CATS b1p1", "#f47230", "s"),
    ("cho:b1p.01:", "CATS b1p.01", "#00b894", "D"),
    ("cho:b.1p1:", "CATS b.1p1", "#f2a600", "^"),
    ("cho:b.1p.01:", "CATS b.1p.01", "#ee7fac", "v"),
]
TEXT, TEXT2, GRID = "#1a1a1a", "#5c5c5c", "#e3e3e3"
# the headline: written from the grades table after each rebuild (it states a finding)
TITLE = ("Tilt recovered only at moderate field dispersion (b1p1, b1p.01). Turbulent runs stay\n"
         "near 50–55° whatever the truth; the very ordered Athena++ run is biased low")


def style(run_id):
    for prefix, label, color, marker in RUN_STYLE:
        if (run_id + ":").startswith(prefix):           # works for 'cho:b1p1' and 'cho:b1p1:t900' 
            return label, color, marker
    return run_id, "#7a7a7a", "x"


@obs.traced("key_result.load_cache")
def load_cache(db):
    """Datasets and estimates with status 'ok', read-only."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    datasets = [dict(r) for r in con.execute(
        "SELECT run_id, legacy_name, code, grid_n, dB_over_B0, M_A, data_status "
        "FROM datasets ORDER BY run_id")]
    est = [dict(r) for r in con.execute(
        "SELECT run_id, angle, rule, gamma_B_true, gamma_obs, gamma_obs_lowS "
        "FROM estimates WHERE status = 'ok' ORDER BY run_id, rule, angle")]
    con.close()
    return {"datasets": datasets, "estimates": est}


# The proposal (15 Oct 2026) pre-registered SEVEN viewing angles (0, 15, ..., 90 deg) and one
# snapshot per run.  That analysis stays the headline; the HW10 extension (5 deg steps, three
# snapshots, bootstrap) is reported next to it, never instead of it.
PREREG_STEP_DEG = 15


def is_primary(dataset):
    """The snapshot used in the pre-registered analysis: data_pipeline.PRIMARY_CATS_RUNS for
    CATS; every other source has one snapshot."""
    import data_pipeline_alexis as pl
    return dataset["code"] != "Cho-ENO" or dataset["legacy_name"] in pl.PRIMARY_CATS_RUNS


def physical_run(run_id):
    """'cho:b1p1:t900' -> 'cho:b1p1'; 'athenapp:1395e2f:Turb.out2.00050' -> 'athenapp:1395e2f'."""
    return run_id.rsplit(":", 1)[0]


def regime_label(dB):
    if dB >= ORDERED_LIMIT:
        return "turbulent"
    return "very ordered (biased low)" if dB < LOW_DISPERSION_LIMIT else "moderate"


@obs.traced("key_result.grade_runs")
def grade_runs(data):
    """grade_with_ci for every physical run x estimator x p_max rule, pooling its snapshots
    (each snapshot's maps are kept apart as bootstrap groups)."""
    rows = []
    meta = {d["run_id"]: d for d in data["datasets"]}
    keys = sorted({(physical_run(e["run_id"]), e["rule"]) for e in data["estimates"]})
    analyses = {
        "pre-registered": lambda e: (is_primary(meta[e["run_id"]])
                                     and e["angle"] % PREREG_STEP_DEG == 0),
        "extended": lambda e: True,
    }
    for analysis, keep in analyses.items():
        for prun, rule in keys:
            pts = [e for e in data["estimates"]
                   if physical_run(e["run_id"]) == prun and e["rule"] == rule and keep(e)]
            if not pts:
                continue
            snaps = sorted({p["run_id"] for p in pts})
            dBs = [meta[r]["dB_over_B0"] for r in snaps]
            for col, label in ESTIMATORS.items():
                g = grade_with_ci([p["gamma_B_true"] for p in pts], [p[col] for p in pts],
                                  groups=[p["run_id"].rsplit(":", 1)[1] for p in pts])
                rows.append({
                    "analysis": analysis,
                    "run": style(prun)[0], "run_id": prun, "n_snapshots": len(snaps),
                    "snapshots": " ".join(r.rsplit(":", 1)[1] for r in snaps),
                    "tilt_step_deg": int(min(np.diff(sorted({p["angle"] for p in pts})))),
                    "grid_n": meta[snaps[0]]["grid_n"],
                    "dB_over_B0": round(float(np.mean(dBs)), 3),
                    "dB_over_B0_range": (f"{min(dBs):.2f}" if min(dBs) == max(dBs)
                                         else f"{min(dBs):.2f}-{max(dBs):.2f}"),
                    "regime": regime_label(float(np.mean(dBs))),
                    "pmax_rule": rule, "estimator": label, "n_maps_30_60": g["n_maps"],
                    "median_signed_err_deg": round(g["median_signed_err"], 2),
                    "median_abs_err_deg": round(g["median_abs_err"], 2),
                    "median_abs_err_ci95": "{:.1f}-{:.1f}".format(*g["median_abs_err_ci"]),
                    "max_abs_err_deg": round(g["max_abs_err"], 2),
                    "slope": round(g["slope"], 3),
                    "slope_ci95": "{:.2f}-{:.2f}".format(*g["slope_ci"]),
                    "snapshot_spread_deg": round(g.get("group_spread_deg", 0.0), 2),
                    "p_pass": round(g["p_pass"], 3),
                    "pass_preregistered": g["pass_preregistered"], "PASS": g["PASS"]})
    return rows


@obs.traced("key_result.write_tables")
def write_tables(rows, data, out):
    out.mkdir(parents=True, exist_ok=True)
    gcsv = out / "tilt_recovery_grades_alexis.csv"
    with open(gcsv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    pcsv = out / "tilt_recovery_points_alexis.csv"
    with open(pcsv, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["run_id", "pmax_rule", "angle", "gamma_B_true", "gamma_obs", "gamma_obs_lowS"])
        for e in data["estimates"]:
            w.writerow([e["run_id"], e["rule"], e["angle"], e["gamma_B_true"],
                        e["gamma_obs"], e["gamma_obs_lowS"]])
    md = out / "tilt_recovery_grades_alexis.md"
    def table(analysis):
        sel = [r for r in rows if r["pmax_rule"] == PRIMARY_RULE and r["analysis"] == analysis]
        out = ["| run | snapshots | tilt step | dB/B0 | regime | estimator | median error (deg) | "
               "median abs error (deg) [95 %] | slope [95 %] | P(pass) | pre-registered test "
               "(median error < 15) | grade |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in sel:
            out.append(f"| {r['run']} | {r['snapshots']} | {r['tilt_step_deg']} | "
                       f"{r['dB_over_B0_range']} | {r['regime']} | {r['estimator']} | "
                       f"{r['median_signed_err_deg']:+.1f} | {r['median_abs_err_deg']:.1f} "
                       f"[{r['median_abs_err_ci95']}] | {r['slope']:.2f} [{r['slope_ci95']}] | "
                       f"{r['p_pass']:.2f} | {'pass' if r['pass_preregistered'] else 'fail'} | "
                       f"{'PASS' if r['PASS'] else 'FAIL'} |")
        return out

    lines = ["## Pre-registered analysis (proposal, 15 Oct 2026)", "",
             f"Seven viewing angles (0-90 deg in {PREREG_STEP_DEG} deg steps), the primary snapshot "
             f"of each run, p_max = map maximum. Pre-registered success: median error < "
             f"{ERR_LIMIT:g} deg over true tilts {TILT_MIN:g}-{TILT_MAX:g} deg. PASS (added in "
             f"Week 7) also requires slope > {SLOPE_MIN:g}. 95 % intervals and P(pass) from 2000 "
             "bootstrap resamples of the maps.", ""] + table("pre-registered") + [
             "", "## Extension (HW10 review, not pre-registered)", "",
             "Every stored tilt (5 deg steps after the HW10 rebuild) and every snapshot of each run, "
             "pooled. Snapshot spread (CSV) = range of the per-snapshot median signed error. "
             "Identical to the table above until the cache is rebuilt with the HW10 method.", ""] + \
        table("extended")
    md.write_text("\n".join(lines) + "\n")
    return {"grades_csv": gcsv, "points_csv": pcsv, "grades_md": md, "n_grade_rows": len(rows)}


@obs.traced("key_result.make_figure")
def make_figure(data, rows, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Pin every setting that changes pixels.  rcdefaults() alone is not enough: the machine
    # that first made the figure injected defaults (font Inter, text.hinting no_hinting,
    # figure.dpi 200) that survive it, so a fresh venv drew a different PNG.
    matplotlib.rcdefaults()
    plt.rcParams.update({"font.family": "DejaVu Sans", "text.hinting": "default",
                         "text.hinting_factor": 8, "figure.dpi": 100, "savefig.dpi": "figure",
                         "text.antialiased": True, "lines.antialiased": True, "font.size": 10,
                         "axes.edgecolor": TEXT2, "axes.labelcolor": TEXT,
                         "xtick.color": TEXT2, "ytick.color": TEXT2, "svg.hashsalt": "alexis"})
    grades = {(r["run_id"], r["estimator"]): r for r in rows
              if r["pmax_rule"] == PRIMARY_RULE and r["analysis"] == "pre-registered"}
    ext = {(r["run_id"], r["estimator"]): r for r in rows
           if r["pmax_rule"] == PRIMARY_RULE and r["analysis"] == "extended"}
    fig, ax = plt.subplots(figsize=(7.8, 5.8), dpi=150)
    ax.fill_between([TILT_MIN, TILT_MAX], [TILT_MIN - ERR_LIMIT, TILT_MAX - ERR_LIMIT],
                    [TILT_MIN + ERR_LIMIT, TILT_MAX + ERR_LIMIT], color="#d9d9d9", alpha=0.6,
                    lw=0, zorder=0, label=f"pass band: ±{ERR_LIMIT:g}° over {TILT_MIN:g}–{TILT_MAX:g}°")
    ax.plot([0, 90], [0, 90], ls="--", lw=1.2, color=TEXT2, zorder=1, label="perfect estimate")
    order = [p for p, *_ in RUN_STYLE]
    pruns = sorted({physical_run(e["run_id"]) for e in data["estimates"]},
                   key=lambda r: next((i for i, p in enumerate(order) if (r + ":").startswith(p)), 99))
    for prun in pruns:
        pts = [e for e in data["estimates"]
               if physical_run(e["run_id"]) == prun and e["rule"] == PRIMARY_RULE]
        if not pts:
            continue
        label, color, marker = style(prun)
        g = grades[(prun, "low-S")]
        gx = ext[(prun, "low-S")]
        angles = sorted({p["angle"] for p in pts})
        x, y, lo, hi = [], [], [], []
        for a in angles:
            at = [p for p in pts if p["angle"] == a]
            x.append(float(np.mean([p["gamma_B_true"] for p in at])))
            v = [p[PRIMARY_EST] for p in at]
            y.append(float(np.mean(v)))
            lo.append(min(v))
            hi.append(max(v))
        if gx["n_snapshots"] > 1:
            ax.fill_between(x, lo, hi, color=color, alpha=0.18, lw=0, zorder=2)
        ax.plot(x, y, color=color, lw=2, marker=marker, ms=5.5, mec="white", mew=1.0,
                markevery=3, zorder=3,
                label=f"{label}  δB/B₀ {gx['dB_over_B0_range']}  pre-reg. "
                      f"{'PASS' if g['PASS'] else 'FAIL'}; ext. P(pass) {gx['p_pass']:.2f}, "
                      f"{gx['n_snapshots']} snap.")
        nudge = -7 if prun.startswith("athenapp:") else 0     # avoid the b1p.01 label
        ax.annotate(label.replace(" (own run)", ""), (x[-1], y[-1]), xytext=(6, nudge),
                    textcoords="offset points", va="center", fontsize=8.5, color=TEXT)
    ax.set_xlim(-2, 106)
    ax.set_ylim(-2, 94)
    ax.set_xticks(range(0, 91, 15))
    ax.set_yticks(range(0, 91, 15))
    ax.set_xlabel("true tilt of the mean field γ_B (deg)")
    ax.set_ylabel("estimated tilt, low-S pixels (deg)")
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.set_title(TITLE, fontsize=11, color=TEXT, loc="left")
    leg = ax.legend(loc="upper left", fontsize=7.3, frameon=False, labelcolor=TEXT)
    leg.set_zorder(5)
    fig.text(0.01, 0.005, "Line: mean over snapshots; band: snapshot range. p_max = map maximum "
             "(pre-registered). Reproduce: make result",
             fontsize=7, color=TEXT2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    png = out / "tilt_recovery_alexis.png"
    fig.savefig(png, metadata={"Software": None})
    plt.close(fig)
    return {"figure": png, "n_runs": len(pruns)}


def compare(dir_a, dir_b):
    """Compare two output folders: tables byte for byte, PNGs by decoded pixels.

    PNG *bytes* depend on the encoder (Pillow 12 and 13 compress the same image
    differently), so a figure counts as reproduced when its pixels are identical.
    Returns 0 if everything matches.
    """
    import matplotlib.image as mpimg
    bad = 0
    for fa in sorted(Path(dir_a).iterdir()):
        fb = Path(dir_b) / fa.name
        if not fb.exists():
            print(f"MISSING in {dir_b}: {fa.name}")
            bad += 1
        elif fa.read_bytes() == fb.read_bytes():
            print(f"identical bytes    {fa.name}")
        elif fa.suffix == ".png":
            a, b = mpimg.imread(fa), mpimg.imread(fb)
            if a.shape == b.shape and np.array_equal(a, b):
                print(f"identical pixels   {fa.name} (PNG encoding differs)")
            else:
                n = int((np.abs(a - b).max(axis=-1) > 0).sum()) if a.shape == b.shape else -1
                print(f"DIFFERENT          {fa.name} ({n} pixels differ)")
                bad += 1
        else:
            print(f"DIFFERENT          {fa.name}")
            bad += 1
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(HERE / "cache" / "pipeline_cache_alexis.db"))
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--log-file", default=None,
                    help="JSON-lines log (default $OBS_LOG_FILE, else logs/key_result_alexis.jsonl)")
    ap.add_argument("--compare", nargs=2, metavar=("DIR_A", "DIR_B"),
                    help="compare two output folders (tables by bytes, PNG by pixels) and exit")
    a = ap.parse_args(argv)
    if a.compare:
        return compare(*a.compare)
    obs.configure("key_result", log_file=a.log_file, project_root=HERE)
    out = Path(a.out)
    data = load_cache(Path(a.db))
    rows = grade_runs(data)
    files = write_tables(rows, data, out)
    fig = make_figure(data, rows, out)
    for r in rows:
        if r["pmax_rule"] == PRIMARY_RULE:
            print(f"{r['analysis']:<15}{r['run']:<20} {r['n_snapshots']} snap dB/B0={r['dB_over_B0_range']:<9} "
                  f"{r['estimator']:<10} median|err|={r['median_abs_err_deg']:5.1f} "
                  f"[{r['median_abs_err_ci95']}] slope={r['slope']:5.2f} [{r['slope_ci95']}] "
                  f"P(pass)={r['p_pass']:.2f} {'PASS' if r['PASS'] else 'FAIL'}")
    print("wrote", ", ".join(str(Path(p).relative_to(HERE)) if Path(p).is_relative_to(HERE) else str(p)
                             for p in (fig["figure"], files["grades_csv"], files["grades_md"],
                                       files["points_csv"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
