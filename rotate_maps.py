"""
rotate_maps.py  --  view one run at several tilt angles and run the estimator.

The line of sight is tilted so the box's mean field goes from lying in the sky
(0 deg) to pointing at the observer (90 deg). Needs firstmap_b.py in the same folder.

Usage (real data):
    python rotate_maps.py --rho b1p1/dens_t900.fits.gz --bx b1p1/magx_t900.fits.gz \
        --by b1p1/magy_t900.fits.gz --bz b1p1/magz_t900.fits.gz --run b1p1_t900

    In Jupyter: %run rotate_maps.py --rho ... (same arguments)

Options:
    --angles 0 15 30 45 60 75 90   tilt angles in degrees (this is the default)
    --selftest                     run the checks only
    --fake                         use a small fake cube instead of files
    --out results_rotated.csv      where rows are appended

How it works
------------
Instead of rotating the cube itself, each sightline is traced through the box
along a tilted direction, sampling the cube with trilinear interpolation.
The box is periodic, so sightlines wrap around its edges. At every sample the
field vector is projected onto the new sky axes (s1, s2) and line of sight (l).
At 0 deg and 90 deg the samples land exactly on grid cells, so the results
match firstmap_b.py's unrotated views; --selftest checks this.

Rotated maps are not periodic at their edges, so the outer ring of pixels is
left out when selecting low-S pixels.
"""

import argparse
import csv
import os
import time

import numpy as np
from scipy.ndimage import map_coordinates

import firstmap_b as fm
from evaluate import grade

UNIT = {"x": np.array([1.0, 0.0, 0.0]),
        "y": np.array([0.0, 1.0, 0.0]),
        "z": np.array([0.0, 0.0, 1.0])}


def choose_axes(B):
    """Box axis closest to the mean field, and an axis perpendicular to it."""
    mean = np.array([B[k].mean() for k in "xyz"])
    field_ax = "xyz"[int(np.argmax(np.abs(mean)))]
    perp_ax = next(k for k in "zyx" if k != field_ax)
    return field_ax, perp_ax, mean


def frame(theta_deg, field_ax, perp_ax):
    """Sky axes s1, s2 and line of sight l, as (x, y, z) unit vectors.

    theta = 0: line of sight along perp_ax, field axis lies in the sky.
    theta = 90: line of sight along the field axis.
    """
    t = np.radians(theta_deg)
    b, e = UNIT[field_ax], UNIT[perp_ax]
    l = np.sin(t) * b + np.cos(t) * e
    s1 = np.cos(t) * b - np.sin(t) * e
    s2 = np.cross(l, s1)
    return s1, s2, l


def rotated_maps(rho, B, theta_deg, field_ax, perp_ax, p0=fm.P0_TRUE, order=1):
    """N, Q, U, p and density-weighted cos^2(gamma) for a tilted line of sight."""
    n = rho.shape[0]
    if rho.shape != (n, n, n):
        raise ValueError(f"cube must be n x n x n, got {rho.shape}")
    s1, s2, l = frame(theta_deg, field_ax, perp_ax)

    c = (n - 1) / 2.0
    offs = np.arange(n) - c
    UU, VV = np.meshgrid(offs, offs, indexing="ij")          # map pixel (u, v)
    base = c + UU[None] * s1[:, None, None] + VV[None] * s2[:, None, None]

    N = np.zeros((n, n)); Q = np.zeros((n, n)); U = np.zeros((n, n))
    N2 = np.zeros((n, n)); W = np.zeros((n, n))
    fields = (rho, B["x"], B["y"], B["z"])

    for w in offs:                                            # step along the sightline
        P = base + w * l[:, None, None]                       # positions in (x, y, z)
        coords = [P[2], P[1], P[0]]                           # numpy axis order (z, y, x)
        r, bx, by, bz = (map_coordinates(f, coords, order=order, mode="grid-wrap")
                         for f in fields)
        b1 = s1[0] * bx + s1[1] * by + s1[2] * bz
        b2 = s2[0] * bx + s2[1] * by + s2[2] * bz
        bl = l[0] * bx + l[1] * by + l[2] * bz
        btot2 = b1**2 + b2**2 + bl**2
        cos2g = (b1**2 + b2**2) / btot2
        N += r
        Q += r * (b2**2 - b1**2) / btot2
        U += r * 2 * b1 * b2 / btot2
        N2 += r * (cos2g - 2.0 / 3.0)
        W += r * cos2g

    p = p0 * np.sqrt(Q**2 + U**2) / (N - p0 * N2)
    return N, Q, U, p, W / N


def true_gamma(mean_B, theta_deg, field_ax, perp_ax):
    """Chen's gamma_B: tilt of the box-averaged field relative to the new sky."""
    _, _, l = frame(theta_deg, field_ax, perp_ax)
    m = np.asarray(mean_B, dtype=float)
    s = abs(float(m @ l)) / np.linalg.norm(m)
    return float(np.degrees(np.arcsin(np.clip(s, 0.0, 1.0))))


# ---------------------------------------------------------------- checks
def selftest():
    p0 = fm.P0_TRUE

    # 1. uniform field tilted by 45 deg must follow Chen eq. 7 exactly
    n = 16
    rho = np.ones((n, n, n))
    B = {"x": np.ones((n, n, n)), "y": np.zeros((n, n, n)), "z": np.zeros((n, n, n))}
    for th in (0, 30, 45, 60, 90):
        _, _, _, p, _ = rotated_maps(rho, B, th, "x", "z")
        c2 = np.cos(np.radians(th))**2
        expected = p0 * c2 / (1 - p0 * (c2 - 2 / 3))
        assert np.allclose(p, expected, atol=1e-10), (th, p.mean(), expected)
        assert abs(true_gamma([1, 0, 0], th, "x", "z") - th) < 1e-9
    print("[check] uniform field at 0/30/45/60/90 deg matches Chen eq. 7   OK")

    # 2. 0 deg and 90 deg must reproduce the unrotated maps from firstmap_b.py
    rho, B = fm.fake_cube(n=48)
    _, _, _, p_z, _ = fm.stokes_maps(rho, B, los_axis=0)      # look down z: map[y, x]
    _, _, _, p_x, _ = fm.stokes_maps(rho, B, los_axis=2)      # look down x: map[z, y]
    _, _, _, p0deg, _ = rotated_maps(rho, B, 0, "x", "z")    # map[u=x, v=y]
    _, _, _, p90, _ = rotated_maps(rho, B, 90, "x", "z")     # map[u=-z, v=y]
    assert np.allclose(p0deg, p_z.T, atol=1e-8), np.abs(p0deg - p_z.T).max()
    assert np.allclose(p90, p_x[::-1, :], atol=1e-8), np.abs(p90 - p_x[::-1, :]).max()
    print("[check] 0 deg and 90 deg reproduce the unrotated z and x views  OK")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Tilted-view estimator test")
    ap.add_argument("--rho"); ap.add_argument("--bx")
    ap.add_argument("--by"); ap.add_argument("--bz")
    ap.add_argument("--run", default="unnamed")
    ap.add_argument("--angles", type=float, nargs="+",
                    default=[0, 15, 30, 45, 60, 75, 90])
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default="results_rotated.csv")
    ap.add_argument("--pmax-percentile", type=float, default=fm.PMAX_PERCENTILE,
                    help="percentile of p used as p_max (100 = map maximum, as in Chen et al. 2019)")
    a = ap.parse_args()
    fm.PMAX_PERCENTILE = a.pmax_percentile

    selftest()
    if a.selftest:
        return

    if a.fake:
        rho, B = fm.fake_cube(n=64)
        run = "FAKE_meanB_along_x"
    else:
        if not all([a.rho, a.bx, a.by, a.bz]):
            ap.error("give --rho --bx --by --bz, or use --fake / --selftest")
        rho, B = fm.load_cubes(a.rho, a.bx, a.by, a.bz)
        run = a.run
    if a.pmax_percentile != 99.5:
        run = f"{run}_pmax{a.pmax_percentile:g}"     # keep results with other p_max rules separate

    field_ax, perp_ax, mean = choose_axes(B)
    print(f"\nrun: {run}   shape: {rho.shape}   p_max rule: "
          f"{'map maximum' if a.pmax_percentile == 100 else f'{a.pmax_percentile:g}th percentile'}")
    print(f"mean field: <Bx>={mean[0]:+.4f} <By>={mean[1]:+.4f} <Bz>={mean[2]:+.4f}"
          f"  -> field along {field_ax}; tilting toward {perp_ax}")

    rows = []
    hdr = (f"{'angle':>5} {'true':>6} {'g_2D':>6} {'p_max':>7} {'p0_der':>7} {'<S>':>6} "
           f"{'g_obs':>6} {'lowS':>6} {'err':>6} {'errLowS':>7} "
           f"{'p0known':>7} {'p0k_lowS':>8} {'sec':>5}")
    print("\n" + hdr)
    for th in a.angles:
        t0 = time.time()
        N, Q, U, p, cos2g_2d = rotated_maps(rho, B, th, field_ax, perp_ax)
        truth = true_gamma(mean, th, field_ax, perp_ax)
        r = fm.estimate_from_maps(Q, U, p, cos2g_2d, truth, mask_border=True)
        r = {"run": run, "angle": th, "field_axis": field_ax, **r}
        rows.append(r)
        print(f"{th:5.0f} {r['gamma_B_true']:6.1f} {r['gamma_2D_peak']:6.1f} "
              f"{r['p_max']:7.4f} {r['p0_derived']:7.4f} {r['median_S_deg']:6.2f} "
              f"{r['gamma_obs']:6.1f} {r['gamma_obs_lowS']:6.1f} "
              f"{r['err']:+6.1f} {r['err_lowS']:+7.1f} "
              f"{r['gamma_obs_truep0']:7.1f} {r['gamma_obs_truep0_lowS']:8.1f} "
              f"{time.time() - t0:5.1f}", flush=True)

    truths = [r["gamma_B_true"] for r in rows]
    print("\ngrade over true tilts 30-60 deg (see evaluate.py):")
    for col, label in (("gamma_obs", "all pixels"), ("gamma_obs_lowS", "low-S")):
        try:
            g = grade(truths, [r[col] for r in rows])
        except ValueError as e:
            print(f"  {label:>10}: not graded ({e})")
            continue
        print(f"  {label:>10}: median err {g['median_signed_err']:+.1f}, "
              f"median |err| {g['median_abs_err']:.1f}, slope {g['slope']:.2f}  "
              f"-> {'PASS' if g['PASS'] else 'FAIL'}"
              f"{'' if g['PASS'] == g['pass_preregistered'] else '  (pre-registered test alone: ' + ('pass' if g['pass_preregistered'] else 'fail') + ')'}")

    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"appended {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
