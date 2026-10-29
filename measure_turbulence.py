"""
measure_turbulence.py  --  measure how turbulent and how magnetized a run is,
check its field labelling, and save the numbers to turbulence.csv.

Collects the notebook checks into one reproducible script:
  * div B check: is the x/y/z labelling of the field files right?
  * anisotropy check: does the gas vary most slowly along the mean field?
  * dB/B0: turbulent field relative to the mean field (the number that best
    predicted whether the tilt estimator works)
  * density spread sigma(rho)/<rho> and max/mean (collapse indicator)
  * sonic and Alfven Mach numbers, if velocity files are given
    (README definition: mean over cells of |v|/c_s and |v|/v_A, v_A = |B|/sqrt(rho))

Usage (Cho-ENO, with velocities; c_s is read from the run name, p -> sqrt(p)):
    %run measure_turbulence.py --run b1p1 \
        --rho b1p1/dens_t900.fits.gz --bx b1p1/magx_t900.fits.gz \
        --by b1p1/magy_t900.fits.gz --bz b1p1/magz_t900.fits.gz \
        --vx b1p1/velx_t900.fits.gz --vy b1p1/vely_t900.fits.gz --vz b1p1/velz_t900.fits.gz

Usage (field and density only, e.g. AREPO cubes from prepare_arepo.py):
    %run measure_turbulence.py --run AREPO_B30_snap073 \
        --rho arepo_B30/grid256/dens.fits --bx arepo_B30/grid256/magx.fits \
        --by arepo_B30/grid256/magy.fits --bz arepo_B30/grid256/magz.fits

Freeze a stage's results (copies results_rotated.csv and writes its grades):
    %run measure_turbulence.py --freeze stageA
      -> stageA_results.csv, stageA_grades.csv

Self-test: python measure_turbulence.py --selftest
"""

import argparse
import csv
import os
import re
import shutil

import numpy as np

import firstmap_b as fm
from prepare_arepo import match_components

AXIS_OF = {"z": 0, "y": 1, "x": 2}       # firstmap_b convention


def sound_speed_from_name(run):
    """Cho-ENO names encode pressure: b1p1 -> p=1, b1p.032 -> 0.032. c_s = sqrt(p)."""
    m = re.match(r"b\.?\d+p(\.?\d+)", run)        # only Cho-ENO names like b1p.01
    if not m:
        return None
    s = m.group(1)
    return float(np.sqrt(float("0" + s if s.startswith(".") else s)))


def roughness(f, ax):
    return float(np.abs(np.roll(f, -1, ax) - f).mean())


def measure(rho, B, v=None, cs=None):
    out = {"shape": "x".join(map(str, rho.shape))}
    mean = {k: float(B[k].mean()) for k in "xyz"}
    field_ax = max(mean, key=lambda k: abs(mean[k]))
    B0 = float(np.sqrt(sum(m**2 for m in mean.values())))
    dB = float(np.sqrt(sum(((B[k] - mean[k])**2).mean() for k in "xyz")))
    out.update({f"meanB{k}": mean[k] for k in "xyz"})
    out.update(field_axis=field_ax, B0=B0, dB_over_B0=dB / B0,
               rho_sigma_over_mean=float(rho.std() / rho.mean()),
               rho_max_over_mean=float(rho.max() / rho.mean()))

    # div B: labelled components [Bz, By, Bx] belong on array axes (0, 1, 2)
    perm, best, second, _ = match_components([B["z"], B["y"], B["x"]])
    labels_ok = perm == (0, 1, 2)
    out.update(divB_labels_ok=labels_ok, divB_ratio=second / best if labels_ok else
               float("nan"))

    # anisotropy: smoothest direction of log-density and of field fluctuations
    lr = np.log(rho)
    r_rho = {k: roughness(lr, AXIS_OF[k]) for k in "xyz"}
    r_B = {k: sum(roughness(B[c] - mean[c], AXIS_OF[k]) for c in "xyz") for k in "xyz"}
    out.update(smoothest_rho=min(r_rho, key=r_rho.get), smoothest_B=min(r_B, key=r_B.get))
    out["anisotropy_ok"] = out["smoothest_rho"] == field_ax == out["smoothest_B"]

    out["c_s"], out["M_s"], out["M_A"] = cs, float("nan"), float("nan")
    if v is not None:
        speed = np.sqrt(v["x"]**2 + v["y"]**2 + v["z"]**2)
        vA = np.sqrt(B["x"]**2 + B["y"]**2 + B["z"]**2) / np.sqrt(rho)
        out["M_A"] = float(np.mean(speed / vA))
        if cs:
            out["M_s"] = float(np.mean(speed / cs))
    return out


def report(run, r):
    print(f"\nrun: {run}   shape: {r['shape']}")
    print(f"mean field: <Bx>={r['meanBx']:+.4g} <By>={r['meanBy']:+.4g} "
          f"<Bz>={r['meanBz']:+.4g}  -> along {r['field_axis']}")
    print(f"dB/B0 = {r['dB_over_B0']:.3f}   sigma(rho)/<rho> = {r['rho_sigma_over_mean']:.3f}"
          f"   max/mean = {r['rho_max_over_mean']:.3g}")
    if r["divB_labels_ok"]:
        print(f"div B:      labels correct, {r['divB_ratio']:.1f}x better than the next pairing"
              f"{'  (weak, see anisotropy)' if r['divB_ratio'] < 3 else ''}")
    else:
        print("div B:      LABELS LOOK WRONG: another x/y/z pairing has smaller div B")
    print(f"anisotropy: smoothest along {r['smoothest_rho']} (density), "
          f"{r['smoothest_B']} (field)  -> {'consistent' if r['anisotropy_ok'] else 'NOT consistent'}")
    if not np.isnan(r["M_A"]):
        ms = "n/a (no c_s)" if np.isnan(r["M_s"]) else f"{r['M_s']:.2f} (c_s = {r['c_s']:.3g})"
        print(f"M_s = {ms}   M_A = {r['M_A']:.2f}")


def append_csv(path, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"appended to {path}")


def freeze(stage, results="results_rotated.csv"):
    from evaluate import grade_csv
    shutil.copy(results, f"{stage}_results.csv")
    grade_csv(results).to_csv(f"{stage}_grades.csv", index=False)
    print(f"wrote {stage}_results.csv and {stage}_grades.csv")


# ---------------------------------------------------------------- self-test
def selftest():
    import tempfile
    from astropy.io import fits
    assert sound_speed_from_name("b1p1") == 1.0
    assert abs(sound_speed_from_name("b.1p.01_t750") - 0.1) < 1e-12
    assert abs(sound_speed_from_name("b1p.032") - np.sqrt(0.032)) < 1e-12
    assert sound_speed_from_name("AREPO_B30_snap073") is None

    rng = np.random.default_rng(3)
    n = 48
    d = lambda a, ax: (np.roll(a, -1, ax) - np.roll(a, 1, ax)) / 2.0
    k = np.fft.fftfreq(n)
    kk = np.sqrt(k[:, None, None]**2 + k[None, :, None]**2 + (3 * k[None, None, :])**2)
    kk[0, 0, 0] = 1                                  # stretched along axis 2 (x)
    sm = lambda: np.real(np.fft.ifftn(np.fft.fftn(rng.standard_normal((n, n, n))) * kk**-2.5))
    A0, A1, A2 = sm(), sm(), sm()
    Bz = d(A2, 1) - d(A1, 2); By = d(A0, 2) - d(A2, 0); Bx = d(A1, 0) - d(A0, 1)
    s = 0.5 / np.sqrt(np.mean(Bx**2 + By**2 + Bz**2))
    B = {"x": Bx * s + 1.0, "y": By * s, "z": Bz * s}   # mean field along x, dB/B0 = 0.5
    rho = np.exp(0.3 * sm() / sm().std())
    v = {c: 0.7 * rng.standard_normal((n, n, n)) for c in "xyz"}

    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for name, arr in [("rho", rho), ("bx", B["x"]), ("by", B["y"]), ("bz", B["z"])]:
            paths[name] = os.path.join(tmp, f"{name}.fits")
            fits.PrimaryHDU(arr.astype(np.float32)).writeto(paths[name])
        rho2, B2 = fm.load_cubes(paths["rho"], paths["bx"], paths["by"], paths["bz"])
        r = measure(rho2, B2, v=v, cs=1.0)
        assert r["field_axis"] == "x" and abs(r["dB_over_B0"] - 0.5) < 1e-3, r
        assert r["divB_labels_ok"] and r["anisotropy_ok"], r
        assert r["M_s"] > 0 and r["M_A"] > 0
        # swapped labels must be caught
        r_bad = measure(rho2, {"x": B2["y"], "y": B2["x"], "z": B2["z"]})
        assert not r_bad["divB_labels_ok"]
    print("[check] c_s from Cho-ENO run names                      OK")
    print("[check] dB/B0, field axis, div B and anisotropy checks  OK")
    print("[check] swapped field labels are detected               OK")


def main():
    ap = argparse.ArgumentParser()
    for f in ("rho", "bx", "by", "bz", "vx", "vy", "vz"):
        ap.add_argument(f"--{f}")
    ap.add_argument("--run", default="unnamed")
    ap.add_argument("--cs", type=float, help="sound speed (default: from Cho-ENO run name)")
    ap.add_argument("--out", default="turbulence.csv")
    ap.add_argument("--freeze", metavar="STAGE", help="save results_rotated.csv and grades as STAGE_*")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return
    if a.freeze:
        freeze(a.freeze)
        return
    if not all([a.rho, a.bx, a.by, a.bz]):
        ap.error("give --rho --bx --by --bz (and optionally --vx --vy --vz), or --freeze / --selftest")

    rho, B = fm.load_cubes(a.rho, a.bx, a.by, a.bz)
    v = None
    if any([a.vx, a.vy, a.vz]):
        if not all([a.vx, a.vy, a.vz]):
            ap.error("give all three of --vx --vy --vz")
        from astropy.io import fits
        v = {c: fits.getdata(f).astype(np.float64) for c, f in zip("xyz", (a.vx, a.vy, a.vz))}
    cs = a.cs if a.cs is not None else sound_speed_from_name(a.run)

    r = measure(rho, B, v=v, cs=cs)
    report(a.run, r)
    append_csv(a.out, {"run": a.run, **r})


if __name__ == "__main__":
    main()
