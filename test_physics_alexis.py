"""
test_physics_alexis.py  --  physics invariants of the estimator code (HW10 review).

Each test is a property the physics guarantees, independent of any stored result:

  * Chen et al. (2019) eq. 7 for a uniform field, eqs. 8-10 inversion round trip
  * no map can exceed the eq. 8 ceiling, so the derived p0 never exceeds the true p0
  * the viewing geometry does not depend on which box axis the mean field lies along
  * Mach numbers use the standard definitions (measure_turbulence == pipeline)
  * the server's three-way regime rule (PHYSICS_REVIEW_alexis.md, finding 1)
  * the p_max rule passed explicitly gives the same answer as the module setting

    pytest test_physics_alexis.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import firstmap_b as fm            # noqa: E402
import rotate_maps as rm           # noqa: E402

P0 = fm.P0_TRUE


def eq7(gamma_deg, p0=P0):
    c = np.cos(np.radians(gamma_deg)) ** 2
    return p0 * c / (1 - p0 * (c - 2 / 3))


def divergence_free_cube(n=32, b0=1.0, db=0.5, seed=0, axis="x"):
    """Mean field b0 along `axis` plus a divergence-free (B = curl A) turbulent part with
    rms db; lognormal density.  Unlike firstmap_b.fake_cube, div B = 0."""
    rng = np.random.default_rng(seed)
    k = np.fft.fftfreq(n)
    kk = np.sqrt(k[:, None, None]**2 + k[None, :, None]**2 + k[None, None, :]**2)
    kk[0, 0, 0] = 1
    sm = lambda: np.real(np.fft.ifftn(np.fft.fftn(rng.standard_normal((n, n, n))) * kk**-2.5))
    d = lambda a, ax: (np.roll(a, -1, ax) - np.roll(a, 1, ax)) / 2
    A0, A1, A2 = sm(), sm(), sm()
    Bz, By, Bx = d(A2, 1) - d(A1, 2), d(A0, 2) - d(A2, 0), d(A1, 0) - d(A0, 1)
    s = db / np.sqrt(np.mean(Bx**2 + By**2 + Bz**2))
    B = {"x": Bx * s, "y": By * s, "z": Bz * s}
    B[axis] = B[axis] + b0
    rho = np.exp(0.4 * sm() / sm().std())
    return rho, B


# ------------------------------------------------------------------ Chen eqs. 7-10
@pytest.mark.parametrize("gamma", [0, 10, 30, 45, 60, 80, 90])
def test_inversion_round_trip(gamma):
    """eq. 10 inverts eq. 7 exactly when p0 is known; eq. 9 inverts eq. 8."""
    assert fm.gamma_from_p(np.array([eq7(gamma)]), P0)[0] == pytest.approx(gamma, abs=1e-6)
    pmax = eq7(0)
    assert pmax == pytest.approx(P0 / (1 - P0 / 3))                     # eq. 8
    assert 3 * pmax / (3 + pmax) == pytest.approx(P0)                    # eq. 9


@pytest.mark.parametrize("theta", [0, 20, 45, 70, 90])
def test_uniform_tilted_field_matches_eq7(theta):
    n = 12
    rho = np.ones((n, n, n))
    B = {"x": np.ones((n, n, n)), "y": np.zeros((n, n, n)), "z": np.zeros((n, n, n))}
    _, Q, U, p, cos2 = rm.rotated_maps(rho, B, theta, "x", "z")
    assert np.allclose(p, eq7(theta), atol=1e-10)
    assert np.allclose(cos2, np.cos(np.radians(theta))**2, atol=1e-10)


@pytest.mark.parametrize("db", [0.1, 0.5, 3.0])
@pytest.mark.parametrize("theta", [0, 45, 90])
def test_no_map_exceeds_the_eq8_ceiling(db, theta):
    """Line-of-sight averaging can only depolarize: p <= p0/(1 - p0/3) everywhere, hence the
    derived p0 <= true p0 (finding 1: why very ordered fields are biased low, never high)."""
    rho, B = divergence_free_cube(n=24, db=db, seed=1)
    fa, pa, _ = rm.choose_axes(B)
    _, Q, U, p, cos2 = rm.rotated_maps(rho, B, theta, fa, pa)
    assert p.max() <= P0 / (1 - P0 / 3) + 1e-12
    r = fm.estimate_from_maps(Q, U, p, cos2, 0.0, mask_border=True, pmax_percentile=100.0)
    assert r["p0_derived"] <= P0 + 1e-12


def test_very_ordered_field_is_biased_low_with_derived_p0():
    """The finding-1 mechanism on a controlled cube: at dB/B0 = 0.1 the estimate with the
    derived p0 is well below the truth at 60 deg, and the known p0 recovers it."""
    rho, B = divergence_free_cube(n=32, db=0.1, seed=2)
    fa, pa, mean = rm.choose_axes(B)
    _, Q, U, p, cos2 = rm.rotated_maps(rho, B, 60, fa, pa)
    truth = rm.true_gamma(mean, 60, fa, pa)
    r = fm.estimate_from_maps(Q, U, p, cos2, truth, mask_border=True, pmax_percentile=100.0)
    assert r["gamma_obs_lowS"] < truth - 10
    assert abs(r["gamma_obs_truep0_lowS"] - truth) < 3


# ------------------------------------------------------------------ geometry
def test_estimate_does_not_depend_on_which_axis_holds_the_field():
    """Relabel the box so the mean field lies along y instead of x (swap the x and y
    array axes and the Bx/By components): every map and estimate must be identical."""
    rho, B = divergence_free_cube(n=24, db=0.5, seed=3, axis="x")
    swap = lambda a: np.swapaxes(a, 1, 2)                        # array axes (z, y, x) -> (z, x, y)
    rho2 = swap(rho)
    B2 = {"x": swap(B["y"]), "y": swap(B["x"]), "z": swap(B["z"])}
    for theta in (0, 30, 60, 90):
        out = []
        for r_, B_ in ((rho, B), (rho2, B2)):
            fa, pa, mean = rm.choose_axes(B_)
            _, Q, U, p, cos2 = rm.rotated_maps(r_, B_, theta, fa, pa)
            out.append((fa, rm.true_gamma(mean, theta, fa, pa),
                        np.sort(p.ravel()), np.sort(cos2.ravel())))
        (fa1, t1, p1, c1), (fa2, t2, p2, c2) = out
        assert (fa1, fa2) == ("x", "y")
        assert t1 == pytest.approx(t2, abs=1e-9)
        assert np.allclose(p1, p2, atol=1e-10) and np.allclose(c1, c2, atol=1e-10)


def test_explicit_pmax_rule_equals_module_setting():
    rho, B = divergence_free_cube(n=24, db=0.5, seed=4)
    fa, pa, mean = rm.choose_axes(B)
    _, Q, U, p, cos2 = rm.rotated_maps(rho, B, 45, fa, pa)
    old = fm.PMAX_PERCENTILE
    try:
        fm.PMAX_PERCENTILE = 100.0
        a = fm.estimate_from_maps(Q, U, p, cos2, 45.0, mask_border=True)
    finally:
        fm.PMAX_PERCENTILE = old
    b = fm.estimate_from_maps(Q, U, p, cos2, 45.0, mask_border=True, pmax_percentile=100.0)
    assert a == b and fm.PMAX_PERCENTILE == old
    assert b["p_max"] == pytest.approx(float(p.max()))


# ------------------------------------------------------------------ Mach numbers
def test_mach_numbers_are_standard_and_match_the_pipeline():
    """PHYSICS_REVIEW_alexis.md, finding 2."""
    import data_pipeline_alexis as pl
    import measure_turbulence as mt
    rng = np.random.default_rng(5)
    for db in (0.2, 5.0):
        rho, B = divergence_free_cube(n=24, db=db, seed=6)
        v = {c: 0.7 * rng.standard_normal(rho.shape) for c in "xyz"}
        r = mt.measure(rho, B, v=v, cs=0.5)
        v_rms = np.sqrt(np.mean(v["x"]**2 + v["y"]**2 + v["z"]**2))
        B0 = np.linalg.norm([B[k].mean() for k in "xyz"])
        assert r["M_A"] == pytest.approx(v_rms / pl.alfven_speed("athenapp", B0, rho.mean()))
        assert r["M_s"] == pytest.approx(v_rms / 0.5)
        # the old cell-mean definition is kept but no longer called M_A; it collapses as dB grows
        if db > 1:
            assert r["M_A_cellmean"] < 0.5 * r["M_A"]


# ------------------------------------------------------------------ regime rule
@pytest.mark.parametrize("dB,expected", [(0.12, "biased_low"), (0.21, "biased_low"),
                                         (0.51, "reliable"), (0.64, "reliable"),
                                         (4.4, "unreliable")])
def test_regime_rule_matches_the_six_tested_runs(dB, expected):
    pytest.importorskip("mcp")
    import mcp_server_alexis as srv
    r = srv.regime(dB)
    assert r["reliability"] == expected
    assert r["ordered_field_dominates"] is (dB < 1)


# ------------------------------------------------------------------ pre-registration
def test_key_result_headline_is_the_preregistered_analysis():
    """The proposal fixed 7 viewing angles (15 deg steps) and one snapshot per run: the
    'pre-registered' rows must use exactly that, whatever else the cache holds."""
    import make_key_result_alexis as kr
    data = kr.load_cache(HERE / "cache" / "pipeline_cache_alexis.db")
    rows = kr.grade_runs(data)
    pre = [r for r in rows if r["analysis"] == "pre-registered"]
    assert pre and all(r["tilt_step_deg"] == 15 and r["n_snapshots"] == 1 for r in pre)
    assert all(r["n_maps_30_60"] == 3 for r in pre)                 # 30, 45, 60 deg
    b1p1 = [r for r in pre if r["run_id"] == "cho:b1p1" and r["estimator"] == "low-S"
            and r["pmax_rule"] == "max"][0]
    assert b1p1["PASS"] and b1p1["median_abs_err_deg"] == pytest.approx(1.9, abs=0.05)
