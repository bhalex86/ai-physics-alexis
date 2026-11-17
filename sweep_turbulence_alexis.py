"""
sweep_turbulence_alexis.py  --  does the Athena++ failure depend on how ordered the field is?

Run from the hw9 folder:

    python sweep_turbulence_alexis.py
    python sweep_turbulence_alexis.py data/athena/Turb.out2.00050.athdf

The turbulent part of the field, dB = B - <B>, is multiplied by k while the mean field <B> and the
density stay fixed (div B stays 0).  For each k the script prints how ordered the box is (dB/B0),
how flat the flattest sightline is at a true tilt of 60 deg, the p0 derived from p_max (true p0 is
0.15), the low-S estimates at 30/45/60 deg, and the Week 8 grade (median |error| < 15 deg AND
slope > 0.5).

PREDICTION (written before running): if the failure is caused by too few flat sightlines, then as k
grows the flattest pixel gets flatter, the derived p0 rises toward 0.15, and the error at 60 deg
shrinks, until turbulence itself breaks the method (estimates go flat, slope falls).

CAVEAT: this changes the field of ONE snapshot; density and velocity do not respond. It is a
controlled probe, not a simulation.  Nothing is written to disk.
"""
import sys

import numpy as np

import data_pipeline_alexis as pl
import firstmap_b as fm
import rotate_maps as rm
from evaluate import grade

PATH = sys.argv[1] if len(sys.argv) > 1 else "data/athena/Turb.out2.00050.athdf"
KS = (0.0, 0.5, 1, 2, 3, 5, 10, 20)

rho, B, vel, meta = pl.read_athdf(PATH)
fm.PMAX_PERCENTILE = 100.0                                  # the pre-registered rule: p_max = map maximum
mean = {a: B[a].mean() for a in "xyz"}
m_vec = np.array([mean[a] for a in "xyz"])

print(f"file: {PATH}   (k = 1 is your original box)")
print(f"{'k':>4}{'dB/B0':>7}{'flattest':>10}{'p0 derived':>12}   {'low-S estimate':>22}   {'error@60':>9}   grade")
print(f"{'':>4}{'':>7}{'@60 deg':>10}{'(true .15)':>12}   {'30':>7}{'45':>7}{'60':>7}")
for k in KS:
    Bk = {a: mean[a] + k * (B[a] - mean[a]) for a in "xyz"}
    rms = np.sqrt(sum(((Bk[a] - mean[a]) ** 2).mean() for a in "xyz"))
    fa, pa, mm = rm.choose_axes(Bk)
    est, true, p0d, flat = [], [], None, None
    for th in (30, 45, 60):
        N, Q, U, p, c = rm.rotated_maps(rho, Bk, th, fa, pa)
        t = rm.true_gamma(mm, th, fa, pa)
        r = fm.estimate_from_maps(Q, U, p, c, t, mask_border=True)
        est.append(r["gamma_obs_lowS"])
        true.append(t)
        if th == 60:
            p0d = r["p0_derived"]
            flat = np.percentile(np.degrees(np.arccos(np.sqrt(np.clip(c, 0, 1)))), 0.5)
    g = grade(true, est)
    print(f"{k:4.1f}{rms / np.linalg.norm(m_vec):7.2f}{flat:10.1f}{p0d:12.3f}   "
          f"{est[0]:7.1f}{est[1]:7.1f}{est[2]:7.1f}   {est[2] - true[2]:+9.1f}   "
          f"{'PASS' if g['PASS'] else 'fail'} (slope {g['slope']:.2f})")

print("\nREAD IT: does the flattest-pixel column fall and the p0 column rise as dB/B0 grows?  "
      "Does the error at 60 deg shrink?  Where (if anywhere) does it PASS?")
