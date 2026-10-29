"""
evaluate.py  --  grade the tilt estimator against the success criteria.

Reads the rows written by rotate_maps.py (results_rotated.csv) and, for each run
and each estimator column, reports:

  1. PRE-REGISTERED (from the proposal): |median signed error| < 15 deg
     over true tilts of 30-60 deg.
  2. median |error| < 15 deg over the same tilts. Stricter: signed errors of
     +20 and -20 would average out in (1) but fail here.
  3. tracking: slope of estimate vs. true tilt over 30-60 deg > 0.5.
     A perfect method has slope 1; a constant guess has slope 0. This catches
     an estimator that is stuck near 45 deg and so "passes" (1) by accident.

A run PASSES only if (2) and (3) both hold. (1) is reported as written in the
proposal, so the pre-registered result stays visible next to the stricter ones.

Usage:
    python evaluate.py                       # reads results_rotated.csv
    python evaluate.py --csv other.csv
    In Jupyter:  %run evaluate.py
"""

import argparse

import numpy as np
import pandas as pd

TILT_MIN, TILT_MAX = 30.0, 60.0
ERR_LIMIT = 15.0          # degrees
SLOPE_MIN = 0.5
TOL = 0.5               # deg; true tilts come from the actual mean field, so 60 may be 59.98
ESTIMATORS = {"gamma_obs": "all pixels", "gamma_obs_lowS": "low-S"}


def grade(true_deg, est_deg, err_limit=ERR_LIMIT, slope_min=SLOPE_MIN,
          tmin=TILT_MIN, tmax=TILT_MAX):
    """Grade one run's estimates. Inputs are matching sequences in degrees."""
    t = np.asarray(true_deg, dtype=float)
    e = np.asarray(est_deg, dtype=float)
    sel = (t >= tmin - TOL) & (t <= tmax + TOL) & np.isfinite(e)
    t, e = t[sel], e[sel]
    if t.size < 2 or np.ptp(t) == 0:
        raise ValueError(f"need at least 2 distinct true tilts in {tmin}-{tmax} deg, got {t.size}")
    err = e - t
    slope = float(np.polyfit(t, e, 1)[0])
    out = {
        "n_maps": int(t.size),
        "median_signed_err": float(np.median(err)),
        "median_abs_err": float(np.median(np.abs(err))),
        "max_abs_err": float(np.max(np.abs(err))),
        "slope": slope,
    }
    out["pass_preregistered"] = abs(out["median_signed_err"]) < err_limit
    out["pass_abs_err"] = out["median_abs_err"] < err_limit
    out["pass_tracking"] = slope > slope_min
    out["PASS"] = out["pass_abs_err"] and out["pass_tracking"]
    return out


def grade_csv(path):
    df = pd.read_csv(path)
    # if a run was rerun, keep its most recent row per angle
    df = df.drop_duplicates(subset=["run", "angle"], keep="last")
    rows = []
    for run, g in df.groupby("run", sort=False):
        for col, label in ESTIMATORS.items():
            r = grade(g["gamma_B_true"], g[col])
            rows.append({"run": run, "estimator": label, **r})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results_rotated.csv")
    a = ap.parse_args()
    res = grade_csv(a.csv)
    show = res[["run", "estimator", "n_maps", "median_signed_err", "median_abs_err",
                "max_abs_err", "slope", "pass_preregistered", "PASS"]]
    with pd.option_context("display.float_format", "{:+.2f}".format,
                           "display.width", 140):
        print(show.to_string(index=False))
    print(f"\nPASS = median |error| < {ERR_LIMIT:g} deg AND slope > {SLOPE_MIN:g}, "
          f"over true tilts {TILT_MIN:g}-{TILT_MAX:g} deg")
    return res


if __name__ == "__main__":
    main()
