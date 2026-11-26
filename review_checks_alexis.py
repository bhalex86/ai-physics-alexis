"""
review_checks_alexis.py  --  recompute every number quoted in PHYSICS_REVIEW_alexis.md and
REPRODUCIBILITY.md that does not come from the committed cache.

So that every quantitative claim in the review is traceable to a committed output.  Writes
results/review_checks_alexis.txt (and logs every step as JSON).  About 1-2 minutes, no
download: it uses synthetic cubes with fixed seeds, the committed Athena++ snapshot and the
committed cache.

    make review-checks
    python review_checks_alexis.py --out results/review_checks_alexis.txt

Sections (the label in brackets is where the number is quoted):
  A  KDE subsample seed noise                        [review finding 5, REPRODUCIBILITY]
  B  interpolation smoothing, half-cell offset test  [review finding 6]
  C  density interpolation: cubic overshoot / mass   [review finding 6]
  D  Alfven Mach number definitions                  [review finding 2]
  E  old vs new method on the Athena++ run           [README, review]
  F  derived p0, S and errors per run (from cache)   [review finding 1, 7]
"""

import argparse
import io
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np                                   # noqa: E402
from scipy.ndimage import map_coordinates, spline_filter   # noqa: E402

import obs_logging_alexis as obs                     # noqa: E402
import data_pipeline_alexis as pl                    # noqa: E402
import firstmap_b as fm                              # noqa: E402
import measure_turbulence as mt                      # noqa: E402
import rotate_maps as rm                             # noqa: E402

SEEDS = obs.seed_everything(42)
ATHDF = HERE / "data" / "athena" / "Turb.out2.00050.athdf"


def estimate(rho, B, th, order=None, offset=0.0, max_n=None, seed=0):
    """Low-S and all-pixel estimates at one tilt; p_max = map maximum."""
    fa, pa, mean = rm.choose_axes(B)
    _, Q, U, p, c2 = rm.rotated_maps(rho, B, th, fa, pa, order=order, offset=offset)
    truth = rm.true_gamma(mean, th, fa, pa)
    if max_n is None:
        r = fm.estimate_from_maps(Q, U, p, c2, truth, mask_border=True, pmax_percentile=100.0)
        return r
    # the pre-HW10 estimator: KDE on a random subsample of max_n pixels
    pmax = float(p.max())
    g = fm.gamma_from_p(p, 3 * pmax / (3 + pmax))
    return {"gamma_obs": fm.kde_peak(g, max_n=max_n, seed=seed)}


@obs.traced("review.A_kde_seed_noise")
def section_a():
    print("A. KDE subsample seed noise (pre-HW10 estimator: 20 000-pixel subsample)")
    print("   synthetic divergence-free cube 192^3, fake_cube(seed=0, db=0.5); all-pixel estimate")
    rho, B = fm.fake_cube(n=192, seed=0, db=0.5)
    fa, pa, mean = rm.choose_axes(B)
    out = {}
    for th in (30, 45, 60):
        _, Q, U, p, c2 = rm.rotated_maps(rho, B, th, fa, pa)
        pmax = float(p.max())
        g = fm.gamma_from_p(p, 3 * pmax / (3 + pmax))
        vals = [fm.kde_peak(g, max_n=20000, seed=s) for s in range(20)]
        full = fm.kde_peak(g)
        out[th] = max(vals) - min(vals)
        print(f"   tilt {th:2d}: {g.size} pixels; 20 seeds give {min(vals):.1f}-{max(vals):.1f} deg "
              f"(spread {out[th]:.1f}); every pixel: {full:.1f}")
    return out


@obs.traced("review.B_interpolation_smoothing")
def section_b():
    print("\nB. Interpolation smoothing: shift every sample by half a cell (forces interpolation")
    print("   even at 0 and 90 deg) and compare with no shift. order 1 = trilinear B (pre-HW10),")
    print("   order 3 = cubic B (HW10); density is trilinear in both.")
    rho_a, B_a, _, _ = pl.read_athdf(ATHDF)
    cubes = {"Athena++ 64^3 (dB/B0 0.21)": (rho_a, B_a),
             "rough synthetic 64^3": fm.fake_cube(n=64, seed=0, db=0.6)}
    out = {}
    for name, (rho, B) in cubes.items():
        for order in (1, 3):
            parts = []
            for th in (0, 45, 90):
                a = estimate(rho, B, th, order=order)
                b = estimate(rho, B, th, order=order, offset=0.5)
                dS = 100 * (b["median_S_deg"] / a["median_S_deg"] - 1)
                dg = max(abs(b["gamma_obs"] - a["gamma_obs"]),
                         abs(b["gamma_obs_lowS"] - a["gamma_obs_lowS"]))
                out[(name, order, th)] = (dS, dg)
                parts.append(f"{th:2d} deg: S {dS:+5.1f} %, estimate {dg:4.1f} deg")
            print(f"   {name:<28} order {order}:  " + " | ".join(parts))
    return {f"{k[0]} o{k[1]} {k[2]}": v for k, v in out.items()}


@obs.traced("review.C_density_interpolation")
def section_c():
    print("\nC. Density interpolation on a lognormal field (64^3, spectrum k^-1.5), 400 000 random points")
    rng = np.random.default_rng(0)
    n = 64
    k = np.fft.fftfreq(n)
    kk = np.sqrt(k[:, None, None]**2 + k[None, :, None]**2 + k[None, None, :]**2)
    kk[0, 0, 0] = 1
    out = {}
    for sig in (0.5, 1.0, 2.0):
        f = np.real(np.fft.ifftn(np.fft.fftn(rng.standard_normal((n, n, n))) * kk**-1.5))
        f /= f.std()
        rho = np.exp(sig * f)
        pts = rng.uniform(0, n, (3, 400000))
        lin = map_coordinates(rho, pts, order=1, mode="grid-wrap")
        cub = map_coordinates(spline_filter(rho, 3, mode="grid-wrap"), pts, order=3,
                              mode="grid-wrap", prefilter=False)
        lcub = np.exp(map_coordinates(spline_filter(np.log(rho), 3, mode="grid-wrap"), pts,
                                      order=3, mode="grid-wrap", prefilter=False))
        m = rho.mean()
        out[sig] = {"max_over_mean": rho.max() / m, "neg_frac_cubic": float(np.mean(cub < 0)),
                    "mean_linear": lin.mean() / m, "mean_cubic": cub.mean() / m,
                    "mean_logcubic": lcub.mean() / m}
        print(f"   sigma_ln {sig}: max/mean {rho.max() / m:6.0f}   mean/true: linear "
              f"{lin.mean() / m:.4f}, cubic {cub.mean() / m:.4f} (negative samples "
              f"{100 * np.mean(cub < 0):.1f} %), log-cubic {lcub.mean() / m:.4f}")
    return out


@obs.traced("review.D_mach_numbers")
def section_d():
    print("\nD. Alfven Mach number: standard v_rms/v_A0 vs the pre-HW10 cell mean <|v|/v_A,local>")
    rng = np.random.default_rng(0)
    out = {}
    for db in (0.2, 1.0, 4.4):
        rho, B = fm.fake_cube(n=48, seed=1, db=db)
        v = {c: 0.7 * rng.standard_normal(rho.shape) for c in "xyz"}
        r = mt.measure(rho, B, v=v, cs=1.0)
        out[db] = (r["dB_over_B0"], r["M_A_cellmean"], r["M_A"])
        print(f"   synthetic, dB/B0 = {r['dB_over_B0']:4.2f}: cell mean {r['M_A_cellmean']:.2f}, "
              f"standard {r['M_A']:.2f}")
    rho, B, v, _ = pl.read_athdf(ATHDF)
    import json
    cs = json.loads((HERE / "data" / "athena" / "athena_run_provenance.json").read_text()
                    )["key_parameters"]["iso_sound_speed"]
    r = mt.measure(rho, B, v=v, cs=cs)
    out["athena"] = (r["dB_over_B0"], r["M_A_cellmean"], r["M_A"])
    print(f"   Athena++ run, dB/B0 = {r['dB_over_B0']:4.2f}: cell mean {r['M_A_cellmean']:.3f}, "
          f"standard {r['M_A']:.3f}; M_s cell mean {r['M_s_cellmean']:.3f}, standard {r['M_s']:.3f}")
    return out


@obs.traced("review.E_athena_old_vs_new")
def section_e():
    print("\nE. Athena++ run: pre-HW10 method (trilinear B) vs HW10 method (cubic B), low-S, p_max = max")
    print("   (64^2 maps have 4096 pixels, below the old 20 000-pixel subsample, so the KDE is unchanged)")
    rho, B, _, _ = pl.read_athdf(ATHDF)
    worst = 0.0
    for th in (0, 15, 30, 45, 60, 75, 90):
        a = estimate(rho, B, th, order=1)
        b = estimate(rho, B, th, order=3)
        d = b["gamma_obs_lowS"] - a["gamma_obs_lowS"]
        worst = max(worst, abs(d))
        print(f"   {th:2d} deg: old {a['gamma_obs_lowS']:5.1f}  new {b['gamma_obs_lowS']:5.1f}  "
              f"(difference {d:+.1f}); median S old {a['median_S_deg']:.2f} new {b['median_S_deg']:.2f}")
    print(f"   largest change: {worst:.1f} deg")
    return {"largest_change_deg": worst}


@obs.traced("review.F_cache_table")
def section_f():
    print("\nF. From the committed cache (cache/pipeline_cache_alexis.db), p_max = map maximum, low-S")
    con = sqlite3.connect(f"file:{HERE / 'cache' / 'pipeline_cache_alexis.db'}?mode=ro", uri=True)
    q = ("SELECT d.legacy_name, d.dB_over_B0, e.angle, e.p0_derived, e.median_S_deg, e.err_lowS, "
         "e.gamma_obs_truep0_lowS, e.gamma_B_true FROM estimates e JOIN datasets d USING(run_id) "
         "WHERE e.rule = 'max' AND e.angle IN (30, 45, 60) ORDER BY d.dB_over_B0, e.angle")
    print(f"   {'run':<26}{'dB/B0':>6}{'tilt':>6}{'p0 derived':>12}{'median S':>10}{'error':>8}"
          f"{'est. with true p0':>19}")
    n = 0
    for name, dB, ang, p0d, S, err, tp0, true in con.execute(q):
        print(f"   {name:<26}{dB:6.2f}{ang:6d}{p0d:12.3f}{S:10.2f}{err:+8.1f}{tp0:13.1f} (true {true:.0f})")
        n += 1
    con.close()
    print("   AREPO runs: hw8/physics_check_alexis.csv (Week 8)")
    return {"rows": n}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "results" / "review_checks_alexis.txt"))
    ap.add_argument("--log-file", default=None)
    a = ap.parse_args(argv)
    obs.configure("review_checks", log_file=a.log_file, project_root=HERE)
    buf = io.StringIO()
    with redirect_stdout(buf):
        print("review_checks_alexis.py: numbers quoted in PHYSICS_REVIEW_alexis.md and "
              "REPRODUCIBILITY.md\n")
        for f in (section_a, section_b, section_c, section_d, section_e, section_f):
            f()
    text = buf.getvalue()
    print(text, end="")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
