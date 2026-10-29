"""
monte_carlo.py  --  reproduce the Monte Carlo test of Chen et al. (2019), Section 3, Table 1.

Each "sightline" is M random field directions. The tilt gamma and the sky angle psi
are drawn from von Mises distributions (circular analogues of a Gaussian) with
concentrations kappa_gamma and kappa_psi, centred on the mean tilt and on psi = 0.
Density is uniform. Stokes q, u and the correction N2 are summed over the M vectors
(Chen eqs. 1-3) and p computed with p0 = 0.1. Repeating for N sightlines gives a
p distribution whose most probable value (Gaussian KDE peak) is compared with
Chen's Table 1.

Usage:
    %run monte_carlo.py            # all 20 cases, compares with Table 1
    %run monte_carlo.py --N 20000  # faster, noisier
"""

import argparse

import numpy as np
from scipy.stats import gaussian_kde

P0 = 0.1
M, N = 50, 100_000
COS2 = [0.9, 0.7, 0.5, 0.3, 0.1]
CASES = [(16, 16), (1, 16), (16, 1), (1, 1)]           # (kappa_gamma, kappa_psi), Table 1 order

# Chen et al. 2019, Table 1: analytic (eq. 7) and Monte Carlo peak p
TABLE1_ANALYTIC = {0.9: 0.092, 0.7: 0.070, 0.5: 0.049, 0.3: 0.029, 0.1: 0.009}
TABLE1_MC = {
    (16, 16): {0.9: 0.088, 0.7: 0.068, 0.5: 0.049, 0.3: 0.029, 0.1: 0.010},
    (1, 16):  {0.9: 0.068, 0.7: 0.061, 0.5: 0.049, 0.3: 0.037, 0.1: 0.031},
    (16, 1):  {0.9: 0.043, 0.7: 0.034, 0.5: 0.024, 0.3: 0.014, 0.1: 0.005},
    (1, 1):   {0.9: 0.038, 0.7: 0.030, 0.5: 0.024, 0.3: 0.018, 0.1: 0.015},
}


def analytic_p(cos2, p0=P0):
    """Chen eq. 7: uniform field along every sightline."""
    return p0 * cos2 / (1 - p0 * (cos2 - 2.0 / 3.0))


def simulate(cos2_mean, kappa_g, kappa_psi, n=N, m=M, p0=P0, seed=0, axial=True):
    """axial=True: draw the doubled angles 2*gamma and 2*psi from the von Mises
    distribution and halve them, treating field orientations as axes (period 180 deg).
    This is the convention that reproduces Chen's Table 1. axial=False draws gamma
    and psi directly (period 360 deg), which scatters 2*psi far more for the same kappa."""
    rng = np.random.default_rng(seed)
    g_mean = np.arccos(np.sqrt(cos2_mean))
    if axial:
        gam = rng.vonmises(2 * g_mean, kappa_g, size=(n, m)) / 2
        psi = rng.vonmises(0.0, kappa_psi, size=(n, m)) / 2
    else:
        gam = rng.vonmises(g_mean, kappa_g, size=(n, m))
        psi = rng.vonmises(0.0, kappa_psi, size=(n, m))
    c2 = np.cos(gam)**2
    q = (np.cos(2 * psi) * c2).sum(axis=1)
    u = (np.sin(2 * psi) * c2).sum(axis=1)
    N2 = (c2 - 2.0 / 3.0).sum(axis=1)
    return p0 * np.sqrt(q**2 + u**2) / (m - p0 * N2)


def kde_peak(p, log=True):
    """Most probable p. log=True puts the Gaussian kernel on log10(p), as in
    King et al. (2018), which Chen et al. cite for their KDE; log=False uses p itself."""
    if log:
        lp = np.log10(p[p > 0])
        grid = np.linspace(lp.min(), lp.max(), 4001)
        return float(10 ** grid[np.argmax(gaussian_kde(lp)(grid))])
    grid = np.linspace(0, 0.12, 2401)
    return float(grid[np.argmax(gaussian_kde(p)(grid))])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=N)
    ap.add_argument("--linear-kde", action="store_true",
                    help="KDE on p instead of log10(p)")
    ap.add_argument("--direct-angles", action="store_true",
                    help="draw gamma, psi directly instead of the doubled (axial) angles")
    a = ap.parse_args()
    axial = not a.direct_angles

    print("check: analytic eq. 7 vs Table 1 analytic column")
    for c in COS2:
        print(f"  cos^2 gamma = {c}:  {analytic_p(c):.3f}  (Table 1: {TABLE1_ANALYTIC[c]:.3f})")
        assert abs(analytic_p(c) - TABLE1_ANALYTIC[c]) <= 0.0006

    print(f"\nMonte Carlo peak p (M = {M}, N = {a.N}, p0 = {P0}, "
          f"{'doubled (axial) angles' if axial else 'direct angles'}, "
          f"{'KDE on p' if a.linear_kde else 'KDE on log p'}); Chen Table 1 in brackets")
    print(f"{'cos2':>5} " + " ".join(f"{f'kg={kg},kp={kp}':>18}" for kg, kp in CASES))
    worst = 0.0
    for c in COS2:
        cells = []
        for kg, kp in CASES:
            pk = kde_peak(simulate(c, kg, kp, n=a.N, axial=axial), log=not a.linear_kde)
            ref = TABLE1_MC[(kg, kp)][c]
            worst = max(worst, abs(pk - ref))
            cells.append(f"{pk:.3f} [{ref:.3f}]")
        print(f"{c:5.1f} " + " ".join(f"{x:>18}" for x in cells))
    print(f"\nlargest difference from Table 1: {worst:.3f}  "
          f"({'agrees to Table 1 rounding' if worst <= 0.0025 else 'check the larger differences'})")


if __name__ == "__main__":
    main()
