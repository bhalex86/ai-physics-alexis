"""
firstmap_b.py  --  one Cho-ENO run, end to end, no rotation.

Makes synthetic dust-polarization maps along the three box axes and runs the
Chen et al. (2019, MNRAS 485, 3499) inclination estimator on each.

Usage (real data):
    python firstmap_b.py --rho b1p1_dens.fits --bx b1p1_bx.fits \
                        --by b1p1_by.fits --bz b1p1_bz.fits --run b1p1

Usage (self-test on a fake cube, no downloads needed):
    python firstmap_b.py --fake

Conventions
-----------
* FITS arrays load as (axis0, axis1, axis2). By FITS convention this is
  (z, y, x). Check this against the mean field printout: one component of
  the mean field should be much larger than the other two.
* gamma = angle between B and the plane of the sky:
  0 deg = field flat in the sky, 90 deg = field along the line of sight.
* Errors are signed: estimate minus truth.
"""

import argparse
import csv
import os

import numpy as np
from scipy.stats import gaussian_kde

P0_TRUE = 0.15            # intrinsic polarization put into the maps (King et al. 2018)
PMAX_PERCENTILE = 99.5    # p_max from a high percentile, not the single brightest pixel

# array axis -> field component name (FITS order: z, y, x)
AXIS_NAME = {0: "z", 1: "y", 2: "x"}


# ---------------------------------------------------------------- loading
def load_cubes(rho_f, bx_f, by_f, bz_f):
    from astropy.io import fits
    rho = fits.getdata(rho_f).astype(np.float64)
    B = {"x": fits.getdata(bx_f).astype(np.float64),
         "y": fits.getdata(by_f).astype(np.float64),
         "z": fits.getdata(bz_f).astype(np.float64)}
    for k, v in B.items():
        if v.shape != rho.shape:
            raise ValueError(f"B{k} shape {v.shape} != density shape {rho.shape}")
    return rho, B


def fake_cube(n=64, b0=1.0, db=0.3, seed=0):
    """Mean field along x plus smooth random perturbations; lognormal density."""
    rng = np.random.default_rng(seed)
    k = np.fft.fftfreq(n)
    kk = np.sqrt(k[:, None, None]**2 + k[None, :, None]**2 + k[None, None, :]**2)
    kk[0, 0, 0] = 1.0
    filt = kk**(-11 / 6)          # ~Kolmogorov-ish amplitude
    filt[0, 0, 0] = 0.0

    def field():
        f = np.real(np.fft.ifftn(np.fft.fftn(rng.standard_normal((n, n, n))) * filt))
        return f / f.std()

    rho = np.exp(0.5 * field())
    B = {"x": b0 + db * field(), "y": db * field(), "z": db * field()}
    return rho, B


# ---------------------------------------------------------------- physics
def sky_components(B, los_axis):
    """Return (B_sky1, B_sky2, B_los) for a line of sight along array axis los_axis."""
    sky_axes = [a for a in (0, 1, 2) if a != los_axis]
    b1 = B[AXIS_NAME[sky_axes[0]]]
    b2 = B[AXIS_NAME[sky_axes[1]]]
    bl = B[AXIS_NAME[los_axis]]
    return b1, b2, bl


def stokes_maps(rho, B, los_axis, p0=P0_TRUE):
    """Chen et al. 2019 eqs. 1-3, optically thin, homogeneous grain alignment."""
    b1, b2, bl = sky_components(B, los_axis)
    b2tot = b1**2 + b2**2 + bl**2
    cos2g = (b1**2 + b2**2) / b2tot
    N = rho.sum(axis=los_axis)
    Q = (rho * (b2**2 - b1**2) / b2tot).sum(axis=los_axis)
    U = (rho * 2 * b1 * b2 / b2tot).sum(axis=los_axis)
    N2 = (rho * (cos2g - 2.0 / 3.0)).sum(axis=los_axis)
    p = p0 * np.sqrt(Q**2 + U**2) / (N - p0 * N2)
    # density-weighted mean cos^2(gamma) per sightline (Chen eq. 17)
    cos2g_2d = (rho * cos2g).sum(axis=los_axis) / N
    return N, Q, U, p, cos2g_2d


def dispersion_S(Q, U):
    """S from the 8 nearest neighbours, periodic edges (Chen eqs. 5-6). Degrees."""
    acc = np.zeros_like(Q)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di == 0 and dj == 0:
                continue
            Qi = np.roll(np.roll(Q, di, axis=0), dj, axis=1)
            Ui = np.roll(np.roll(U, di, axis=0), dj, axis=1)
            dchi = 0.5 * np.arctan2(Qi * U - Q * Ui, Qi * Q + Ui * U)
            acc += dchi**2
    return np.degrees(np.sqrt(acc / 8.0))


def gamma_from_p(p, p0):
    """Chen eq. 10, clipped to the physical range."""
    cos2 = p * (1 + 2 * p0 / 3) / (p0 * (1 + p))
    cos2 = np.clip(cos2, 0.0, 1.0)
    return np.degrees(np.arccos(np.sqrt(cos2)))


def kde_peak(values, max_n=20000, seed=0):
    """Most probable value via Gaussian KDE (Scott's rule) on a 0-90 deg grid."""
    v = np.asarray(values).ravel()
    v = v[np.isfinite(v)]
    if v.size > max_n:
        v = np.random.default_rng(seed).choice(v, max_n, replace=False)
    if v.size < 10 or np.ptp(v) == 0:
        return float(np.median(v)) if v.size else np.nan
    grid = np.linspace(0, 90, 901)
    return float(grid[np.argmax(gaussian_kde(v)(grid))])


def true_gamma_B(B, los_axis):
    """Chen's gamma_B: tilt of the box-averaged field vector."""
    b1, b2, bl = sky_components(B, los_axis)
    m1, m2, ml = b1.mean(), b2.mean(), bl.mean()
    return float(np.degrees(np.arccos(np.hypot(m1, m2) / np.sqrt(m1**2 + m2**2 + ml**2))))


def estimate_from_maps(Q, U, p, cos2g_2d, truth, p0_true=P0_TRUE, mask_border=False):
    """Chen et al. 2019 estimator applied to a finished set of maps.

    mask_border=True drops the outer ring of pixels from the S selection; use it
    for rotated maps, whose edges are not periodic.
    """
    S = dispersion_S(Q, U)
    if mask_border:
        S[0, :] = S[-1, :] = S[:, 0] = S[:, -1] = np.nan

    pmax = float(np.percentile(p, PMAX_PERCENTILE))
    p0_derived = 3 * pmax / (3 + pmax)                       # Chen eq. 9

    g_obs = gamma_from_p(p, p0_derived)
    g_obs_truep0 = gamma_from_p(p, p0_true)                  # oracle: known p0
    low_S = np.isfinite(S) & (S < np.nanmedian(S))
    g_2d = np.degrees(np.arccos(np.sqrt(np.clip(cos2g_2d, 0, 1))))

    est = kde_peak(g_obs)
    est_lowS = kde_peak(g_obs[low_S])
    return {
        "gamma_B_true": truth,
        "gamma_2D_peak": kde_peak(g_2d),
        "p_max": pmax,
        "p0_derived": p0_derived,
        "median_S_deg": float(np.nanmedian(S)),
        "gamma_obs": est,
        "gamma_obs_lowS": est_lowS,
        "gamma_obs_truep0": kde_peak(g_obs_truep0),
        "gamma_obs_truep0_lowS": kde_peak(g_obs_truep0[low_S]),
        "err": est - truth,
        "err_lowS": est_lowS - truth,
    }


def analyse(rho, B, los_axis, p0_true=P0_TRUE):
    N, Q, U, p, cos2g_2d = stokes_maps(rho, B, los_axis, p0_true)
    S = dispersion_S(Q, U)

    pmax = float(np.percentile(p, PMAX_PERCENTILE))
    p0_derived = 3 * pmax / (3 + pmax)                       # Chen eq. 9

    g_obs = gamma_from_p(p, p0_derived)
    g_obs_truep0 = gamma_from_p(p, p0_true)                  # oracle: known p0
    low_S = S < np.median(S)
    g_2d = np.degrees(np.arccos(np.sqrt(np.clip(cos2g_2d, 0, 1))))

    truth = true_gamma_B(B, los_axis)
    est = kde_peak(g_obs)
    est_lowS = kde_peak(g_obs[low_S])
    return {
        "los": AXIS_NAME[los_axis],
        "gamma_B_true": truth,
        "gamma_2D_peak": kde_peak(g_2d),
        "p_max": pmax,
        "p0_derived": p0_derived,
        "median_S_deg": float(np.median(S)),
        "gamma_obs": est,
        "gamma_obs_lowS": est_lowS,
        "gamma_obs_truep0": kde_peak(g_obs_truep0),
        "gamma_obs_truep0_lowS": kde_peak(g_obs_truep0[low_S]),
        "err": est - truth,
        "err_lowS": est_lowS - truth,
    }


# ---------------------------------------------------------------- checks
def check_uniform_field(p0=P0_TRUE):
    """Uniform in-sky field must give p = p0 / (1 - p0/3) everywhere."""
    n = 8
    rho = np.ones((n, n, n))
    B = {"x": np.ones((n, n, n)), "y": np.zeros((n, n, n)), "z": np.zeros((n, n, n))}
    _, _, _, p, _ = stokes_maps(rho, B, los_axis=0)          # look down z
    expected = p0 / (1 - p0 / 3)
    assert np.allclose(p, expected), (p.mean(), expected)
    # and gamma_from_p must invert it back to 0 deg
    assert np.allclose(gamma_from_p(p, p0), 0.0, atol=1e-6)
    return expected


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rho"); ap.add_argument("--bx")
    ap.add_argument("--by"); ap.add_argument("--bz")
    ap.add_argument("--run", default="unnamed")
    ap.add_argument("--fake", action="store_true", help="self-test on a fake cube")
    ap.add_argument("--out", default="results.csv")
    a = ap.parse_args()

    expected = check_uniform_field()
    print(f"[check] uniform in-sky field: p = {expected:.5f} = p0/(1-p0/3)  OK")

    if a.fake:
        rho, B = fake_cube()
        run = "FAKE_meanB_along_x"
    else:
        if not all([a.rho, a.bx, a.by, a.bz]):
            ap.error("give --rho --bx --by --bz, or use --fake")
        rho, B = load_cubes(a.rho, a.bx, a.by, a.bz)
        run = a.run

    print(f"\nrun: {run}   shape: {rho.shape}")
    print("mean field:  " + "  ".join(f"<B{k}> = {B[k].mean():+.4f}" for k in "xyz"))
    print("rms field:   " + "  ".join(f"rms B{k} = {B[k].std():.4f}" for k in "xyz"))

    rows = []
    for los_axis in (0, 1, 2):
        r = analyse(rho, B, los_axis)
        r["run"] = run
        rows.append(r)

    print(f"\n{'LOS':>3} {'true':>6} {'g_2D':>6} {'p_max':>7} {'p0_der':>7} {'<S>':>6} "
          f"{'g_obs':>6} {'lowS':>6} {'err':>6} {'errLowS':>7} {'g_obs(p0 known)':>16}")
    for r in rows:
        print(f"{r['los']:>3} {r['gamma_B_true']:6.1f} {r['gamma_2D_peak']:6.1f} "
              f"{r['p_max']:7.4f} {r['p0_derived']:7.4f} {r['median_S_deg']:6.2f} "
              f"{r['gamma_obs']:6.1f} {r['gamma_obs_lowS']:6.1f} "
              f"{r['err']:+6.1f} {r['err_lowS']:+7.1f} {r['gamma_obs_truep0']:16.1f}")

    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run"] + [k for k in rows[0] if k != "run"])
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"\nappended {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
