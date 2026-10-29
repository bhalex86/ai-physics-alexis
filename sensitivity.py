"""
sensitivity.py  --  how much do two analysis choices change the tilt estimate?

  1. p_max: the map maximum (Chen et al. 2019) vs. high percentiles
     (99.9, 99.5 = what firstmap_b.py uses, 99).
  2. "most probable value": plain Gaussian KDE (Chen; firstmap_b.py) vs. a
     reflected KDE that respects the 0-90 deg limits, vs. the median.

A plain KDE spills probability past 0 and 90 deg and so pulls the peak away
from the edges; the reflected KDE mirrors the values at 0 and 90 to remove that.

Usage:
    %run sensitivity.py --rho b1p1/dens_t900.fits.gz --bx b1p1/magx_t900.fits.gz \
        --by b1p1/magy_t900.fits.gz --bz b1p1/magz_t900.fits.gz --run b1p1_t900

    --angles 0 30 60 90   (default)     --fake  (test cube)
Results are appended to sensitivity.csv.
"""

import argparse
import csv
import os

import numpy as np
from scipy.stats import gaussian_kde

import firstmap_b as fm
import rotate_maps as rm

PCTS = [100.0, 99.9, 99.5, 99.0]         # 100 = maximum


def kde_peak_reflected(values, max_n=20000, seed=0):
    v = np.asarray(values).ravel()
    v = v[np.isfinite(v)]
    if v.size > max_n:
        v = np.random.default_rng(seed).choice(v, max_n, replace=False)
    if v.size < 10 or np.ptp(v) == 0:
        return float(np.median(v)) if v.size else np.nan
    bw = gaussian_kde(v).factor                     # same Scott bandwidth as the plain KDE
    mirrored = np.concatenate([v, -v, 180.0 - v])
    k = gaussian_kde(mirrored, bw_method=bw * np.std(v) / np.std(mirrored))
    grid = np.linspace(0, 90, 901)
    return float(grid[np.argmax(k(grid))])


def run_angle(rho, B, th, field_ax, perp_ax, mean):
    _, Q, U, p, _ = rm.rotated_maps(rho, B, th, field_ax, perp_ax)
    truth = rm.true_gamma(mean, th, field_ax, perp_ax)
    rows = []
    for pct in PCTS:
        pmax = float(p.max() if pct == 100 else np.percentile(p, pct))
        p0 = 3 * pmax / (3 + pmax)
        g = fm.gamma_from_p(p, p0)
        rows.append({"angle": th, "true": truth, "pmax_rule": "max" if pct == 100 else f"p{pct:g}",
                     "p_max": pmax, "p0": p0,
                     "kde": fm.kde_peak(g), "kde_reflected": kde_peak_reflected(g),
                     "median": float(np.median(g))})
    g_true = fm.gamma_from_p(p, fm.P0_TRUE)
    rows.append({"angle": th, "true": truth, "pmax_rule": "p0 known", "p_max": float("nan"),
                 "p0": fm.P0_TRUE, "kde": fm.kde_peak(g_true),
                 "kde_reflected": kde_peak_reflected(g_true), "median": float(np.median(g_true))})
    return rows


def main():
    ap = argparse.ArgumentParser()
    for f in ("rho", "bx", "by", "bz"):
        ap.add_argument(f"--{f}")
    ap.add_argument("--run", default="unnamed")
    ap.add_argument("--angles", type=float, nargs="+", default=[0, 30, 60, 90])
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--out", default="sensitivity.csv")
    a = ap.parse_args()

    if a.fake:
        rho, B = fm.fake_cube(n=64)
        run = "FAKE"
    else:
        if not all([a.rho, a.bx, a.by, a.bz]):
            ap.error("give --rho --bx --by --bz, or --fake")
        rho, B = fm.load_cubes(a.rho, a.bx, a.by, a.bz)
        run = a.run
    field_ax, perp_ax, mean = rm.choose_axes(B)

    print(f"run: {run}   field along {field_ax}")
    print("estimate (deg) for each p_max rule and peak method; err = estimate - true\n")
    print(f"{'angle':>5} {'true':>5} {'p_max rule':>10} {'p0':>7} "
          f"{'KDE':>6} {'err':>6} {'reflKDE':>7} {'err':>6} {'median':>6} {'err':>6}")
    all_rows = []
    for th in a.angles:
        for r in run_angle(rho, B, th, field_ax, perp_ax, mean):
            r = {"run": run, **r}
            all_rows.append(r)
            t = r["true"]
            print(f"{r['angle']:5.0f} {t:5.1f} {r['pmax_rule']:>10} {r['p0']:7.4f} "
                  f"{r['kde']:6.1f} {r['kde'] - t:+6.1f} {r['kde_reflected']:7.1f} "
                  f"{r['kde_reflected'] - t:+6.1f} {r['median']:6.1f} {r['median'] - t:+6.1f}")
        print()

    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(all_rows)
    print(f"appended {len(all_rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
