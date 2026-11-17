"""
check_p0_athena_alexis.py  --  why does the Chen estimator under-estimate the tilt of my Athena++ box?

Run from the hw9 folder (the one holding firstmap_b.py, rotate_maps.py and data_pipeline_alexis.py):

    python check_p0_athena_alexis.py
    python check_p0_athena_alexis.py data/athena/Turb.out2.00050.athdf     # another file

It prints four checks.  Nothing is written to disk and nothing is downloaded.

  1. FLATTEST PIXEL: the smallest sky-projected tilt in each tilted map, and whether p_max equals
     Chen eq. 7 evaluated at THAT tilt (the mechanism) instead of at tilt 0 (what eq. 9 assumes).
  2. TRUE p0: the same estimator with the known p0 = 0.15 instead of the p0 derived from p_max.
  3. RESOLUTION: the box block-averaged to half resolution.
  4. p_max RULE: the 99.5th percentile instead of the map maximum.
"""
import sys

import numpy as np

import data_pipeline_alexis as pl
import firstmap_b as fm
import rotate_maps as rm

PATH = sys.argv[1] if len(sys.argv) > 1 else "data/athena/Turb.out2.00050.athdf"
TILTS = (30, 45, 60)
P0 = fm.P0_TRUE


def eq7(gamma_deg, p0=P0):
    """Chen eq. 7: polarization fraction of a uniform field tilted by gamma."""
    c = np.cos(np.radians(gamma_deg)) ** 2
    return p0 * c / (1 - p0 * (c - 2 / 3))


def maps(rho, B, th, fa, pa):
    """Rotated maps and true tilt for one viewing angle."""
    N, Q, U, p, c = rm.rotated_maps(rho, B, th, fa, pa)
    return Q, U, p, c


rho, B, vel, meta = pl.read_athdf(PATH)
fa, pa, mean = rm.choose_axes(B)
print(f"file: {PATH}\nshape {rho.shape}, time {meta['time']}, field axis {fa}")
print(f"expected p at tilt 0 for p0={P0}: {eq7(0):.4f}   (eq. 8; this is what eq. 9 assumes p_max is)\n")

# ---------------------------------------------------------------- 1
print("1. FLATTEST PIXEL")
print(f"{'true':>6}{'flattest':>10}{'p_max':>9}{'eq7(flattest)':>15}{'eq7(0 deg)':>12}   verdict")
fm.PMAX_PERCENTILE = 100.0
ok1 = True
for th in TILTS:
    Q, U, p, c = maps(rho, B, th, fa, pa)
    g2 = np.degrees(np.arccos(np.sqrt(np.clip(c, 0, 1))))
    flat = np.percentile(g2, 0.5)                       # 0.5th percentile: robust to one odd pixel
    near_flat = abs(p.max() - eq7(flat)) / eq7(flat) < 0.1
    far_zero = p.max() < 0.9 * eq7(0)
    ok1 &= near_flat and far_zero
    print(f"{rm.true_gamma(mean, th, fa, pa):6.1f}{flat:10.1f}{p.max():9.4f}{eq7(flat):15.4f}{eq7(0):12.4f}   "
          f"{'p_max matches the flattest pixel, not tilt 0' if near_flat and far_zero else 'does NOT match'}")
print("   ->", "CONFIRMED" if ok1 else "NOT confirmed", "\n")

# ---------------------------------------------------------------- 2 and 4 helper


def run(rho_, B_, pct):
    """Low-S estimates (derived p0, true p0) for the three tilts at one p_max rule."""
    fm.PMAX_PERCENTILE = pct
    out = []
    for th in TILTS:
        Q, U, p, c = maps(rho_, B_, th, fa, pa)
        t = rm.true_gamma(mean, th, fa, pa)
        r = fm.estimate_from_maps(Q, U, p, c, t, mask_border=True)
        out.append((t, r["p0_derived"], r["gamma_obs_lowS"], r["gamma_obs_truep0_lowS"]))
    return out


print("2. TRUE p0 (low-S estimate, degrees)")
print(f"{'true':>6}{'p0 derived':>12}{'estimate':>10}{'error':>8}{'  |  estimate with true p0':>27}{'error':>8}")
rows = run(rho, B, 100.0)
for t, p0d, est, est_true in rows:
    print(f"{t:6.1f}{p0d:12.3f}{est:10.1f}{est - t:+8.1f}{est_true:27.1f}{est_true - t:+8.1f}")
ok2 = all(abs(e - t) < 5 for t, _, _, e in rows) and any(abs(e - t) > 10 for t, _, e, _ in rows)
print("   ->", "CONFIRMED: the tilt formula is fine; the p0 derivation is the problem" if ok2 else "NOT confirmed", "\n")

# ---------------------------------------------------------------- 3


def down(a):
    """Average 2x2x2 blocks."""
    n = a.shape[0] // 2
    return a.reshape(n, 2, n, 2, n, 2).mean(axis=(1, 3, 5))


print("3. RESOLUTION (low-S estimate with derived p0)")
half = run(down(rho), {k: down(v) for k, v in B.items()}, 100.0)
print(f"{'true':>6}{'full res':>10}{'half res':>10}")
for (t, _, e_full, _), (_, _, e_half, _) in zip(rows, half):
    print(f"{t:6.1f}{e_full:10.1f}{e_half:10.1f}")
ok3 = all(abs(a[2] - b[2]) < 3 for a, b in zip(rows, half))
print("   ->", "resolution is NOT the cause (estimates agree within 3 deg)" if ok3 else "resolution changes the result", "\n")

# ---------------------------------------------------------------- 4
print("4. p_max RULE (low-S estimate with derived p0)")
alt = run(rho, B, 99.5)
print(f"{'true':>6}{'p_max=max':>11}{'p_max=99.5%':>13}")
for (t, _, e_max, _), (_, _, e_995, _) in zip(rows, alt):
    print(f"{t:6.1f}{e_max:11.1f}{e_995:13.1f}")
ok4 = any(abs(e - t) > 10 for t, _, e, _ in alt)
print("   ->", "the other rule does not fix it" if ok4 else "the other rule fixes it", "\n")

print("SUMMARY:", "all four checks agree with the explanation" if ok1 and ok2 and ok3 and ok4
      else "at least one check disagrees: read the tables above")
