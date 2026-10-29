"""
check_arepo.py  --  two independent checks on the output of prepare_arepo.py.

1. ANISOTROPY: strongly magnetized turbulence is stretched along the mean field,
   so gas and field vary most slowly along the true field axis. Confirms which
   array axis the mean field lies on, independently of the div B test.

2. RESOLUTION: makes the two unrotated views (along the field, and across it)
   at the full 512^3 resolution, reading the HDF5 files a slab at a time,
   and compares the estimator with the same views from the 256^3 FITS cubes.
   If they agree, block-averaging did not change the answer.

Usage:
    %run check_arepo.py --dens arepo_B100/Density_B100snap_156_512.hdf5 \
        --mag arepo_B100/MagneticField_B100snap_156_512.hdf5 \
        --grid arepo_B100/grid256 --map z,y,x

--map gives the label of MagneticField_0, _1, _2 as printed by prepare_arepo.py
("component labels"). For {'MagneticField_0': 'z', 'MagneticField_1': 'y',
'MagneticField_2': 'x'} use --map z,y,x.

Self-test (no downloads): python check_arepo.py --selftest
"""

import argparse
import os

import numpy as np

import firstmap_b as fm

AXIS_OF = {"z": 0, "y": 1, "x": 2}       # label -> numpy array axis (firstmap_b convention)


# ---------------------------------------------------------------- check 1
def anisotropy(grid_dir):
    from astropy.io import fits
    rho = fits.getdata(os.path.join(grid_dir, "dens.fits")).astype(float)
    B = {k: fits.getdata(os.path.join(grid_dir, f"mag{k}.fits")).astype(float) for k in "xyz"}
    mean = {k: B[k].mean() for k in "xyz"}
    field_ax = max(mean, key=lambda k: abs(mean[k]))
    lr = np.log(rho)
    dB = [B[k] - mean[k] for k in "xyz"]

    def roughness(f, ax):                    # mean |difference between neighbours|
        return float(np.abs(np.roll(f, -1, ax) - f).mean())

    print("ANISOTROPY CHECK (smaller = smoother along that direction)")
    print(f"  mean field is labelled along {field_ax}")
    rows = []
    for lab, ax in AXIS_OF.items():
        r_rho = roughness(lr, ax)
        r_B = sum(roughness(f, ax) for f in dB)
        rows.append((lab, r_rho, r_B))
        print(f"  along {lab} (array axis {ax}):  log-density {r_rho:.4f}   field fluctuations {r_B:.3e}")
    smooth_rho = min(rows, key=lambda t: t[1])[0]
    smooth_B = min(rows, key=lambda t: t[2])[0]
    ok = smooth_rho == field_ax and smooth_B == field_ax
    print(f"  smoothest direction: density {smooth_rho}, field {smooth_B}  ->  "
          f"{'CONSISTENT with field along ' + field_ax if ok else 'NOT consistent: check the labels'}")
    return ok, field_ax


# ---------------------------------------------------------------- check 2
def fullres_views(dens_file, mag_file, labels, field_ax, slab=16, p0=fm.P0_TRUE):
    """Unrotated views at full resolution: along the field axis and along one
    perpendicular axis. Returns {los_label: (Q, U, p, cos2g_2d, truth)}."""
    import h5py
    perp_ax = next(k for k in "zyx" if k != field_ax)
    views = {field_ax: AXIS_OF[field_ax], perp_ax: AXIS_OF[perp_ax]}
    acc = {}
    sums = {k: 0.0 for k in "xyz"}
    with h5py.File(dens_file, "r") as hd, h5py.File(mag_file, "r") as hb:
        D = hd["Density"]
        M = {lab: hb[f"MagneticField_{i}"] for i, lab in enumerate(labels)}
        n0, n1, n2 = D.shape
        shapes = {0: (n1, n2), 1: (n0, n2), 2: (n0, n1)}
        for lab, ax in views.items():
            acc[lab] = [np.zeros(shapes[ax]) for _ in range(5)]   # N, Q, U, N2, W
        for i in range(0, n0, slab):
            r = np.asarray(D[i:i + slab], dtype=float)
            b = {k: np.asarray(M[k][i:i + slab], dtype=float) for k in "xyz"}
            for k in "xyz":
                sums[k] += b[k].sum()
            btot2 = b["x"]**2 + b["y"]**2 + b["z"]**2
            for lab, ax in views.items():
                sky = [k for k in ("z", "y", "x") if k != lab]
                b1, b2 = b[sky[0]], b[sky[1]]
                c2 = (b1**2 + b2**2) / btot2
                parts = (r, r * (b2**2 - b1**2) / btot2, r * 2 * b1 * b2 / btot2,
                         r * (c2 - 2.0 / 3.0), r * c2)
                for a, part in zip(acc[lab], parts):
                    s = part.sum(axis=ax)
                    if ax == 0:
                        a += s
                    else:
                        a[i:i + slab] += s
    ncell = n0 * n1 * n2
    mean = np.array([sums[k] / ncell for k in "xyz"])
    out = {}
    for lab, (N, Q, U, N2, W) in acc.items():
        p = p0 * np.sqrt(Q**2 + U**2) / (N - p0 * N2)
        los_comp = abs(mean["xyz".index(lab)])
        truth = float(np.degrees(np.arcsin(min(1.0, los_comp / np.linalg.norm(mean)))))
        out[lab] = (Q, U, p, W / N, truth)
    return out


def compare_resolution(dens_file, mag_file, grid_dir, labels, field_ax):
    from astropy.io import fits
    print("\nRESOLUTION CHECK: full 512^3 vs block-averaged 256^3, unrotated views")
    full = fullres_views(dens_file, mag_file, labels, field_ax)
    rho = fits.getdata(os.path.join(grid_dir, "dens.fits")).astype(float)
    B = {k: fits.getdata(os.path.join(grid_dir, f"mag{k}.fits")).astype(float) for k in "xyz"}
    print(f"  {'view':>14} {'res':>4} {'true':>6} {'p_max':>7} {'<S>':>6} {'g_obs':>6} {'lowS':>6} {'err':>6} {'errLowS':>7}")
    diffs = []
    for lab, (Q, U, p, c2, truth) in full.items():
        rf = fm.estimate_from_maps(Q, U, p, c2, truth)
        _, Qg, Ug, pg, c2g = fm.stokes_maps(rho, B, los_axis=AXIS_OF[lab])
        rg = fm.estimate_from_maps(Qg, Ug, pg, c2g, fm.true_gamma_B(B, AXIS_OF[lab]))
        name = f"down {lab} ({'along' if lab == field_ax else 'across'} B)"
        for res, r in (("512", rf), ("256", rg)):
            print(f"  {name:>14} {res:>4} {r['gamma_B_true']:6.1f} {r['p_max']:7.4f} "
                  f"{r['median_S_deg']:6.2f} {r['gamma_obs']:6.1f} {r['gamma_obs_lowS']:6.1f} "
                  f"{r['err']:+6.1f} {r['err_lowS']:+7.1f}")
        diffs.append(max(abs(rf["gamma_obs"] - rg["gamma_obs"]),
                         abs(rf["gamma_obs_lowS"] - rg["gamma_obs_lowS"])))
    worst = max(diffs)
    print(f"  largest 512-vs-256 difference in the estimate: {worst:.1f} deg  ->  "
          f"{'averaging to 256^3 is fine' if worst < 5 else 'averaging CHANGES the answer: use with care'}")
    return worst


# ---------------------------------------------------------------- self-test
def selftest():
    import tempfile
    import h5py
    import prepare_arepo as pa
    n, rng = 64, np.random.default_rng(2)
    d = lambda a, ax: (np.roll(a, -1, ax) - np.roll(a, 1, ax)) / 2.0
    k = np.fft.fftfreq(n)
    kk = np.sqrt((3 * k[:, None, None])**2 + k[None, :, None]**2 + k[None, None, :]**2)
    kk[0, 0, 0] = 1                                # 3x stretch along axis 0
    sm = lambda: np.real(np.fft.ifftn(np.fft.fftn(rng.standard_normal((n, n, n))) * kk**-2.5))
    Az, Ay, Ax = sm(), sm(), sm()
    B0 = d(Ax, 1) - d(Ay, 2); B1 = d(Az, 2) - d(Ax, 0); B2 = d(Ay, 0) - d(Az, 1)
    s = 0.1 / np.std(B1)
    B0, B1, B2 = B0 * s + 1.0, B1 * s, B2 * s       # mean field along axis 0 (z)
    with tempfile.TemporaryDirectory() as tmp:
        df, mf, gd = (os.path.join(tmp, x) for x in ("d.h5", "m.h5", "g"))
        with h5py.File(df, "w") as h:
            h["Density"] = np.exp(0.4 * sm() / sm().std())
        with h5py.File(mf, "w") as h:
            h["MagneticField_0"], h["MagneticField_1"], h["MagneticField_2"] = B0, B1, B2
        mapping = pa.prepare(df, mf, gd, factor=2, verbose=False)
        labels = [mapping[f"MagneticField_{i}"] for i in range(3)]
        assert labels == ["z", "y", "x"], labels
        ok, field_ax = anisotropy(gd)
        assert ok and field_ax == "z"
        compare_resolution(df, mf, gd, labels, field_ax)
    print("\n[check] anisotropy finds the stretched field axis; full-res path runs   OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dens"); ap.add_argument("--mag"); ap.add_argument("--grid")
    ap.add_argument("--map", help="labels of MagneticField_0,_1,_2, e.g. z,y,x")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not all([a.dens, a.mag, a.grid, a.map]):
        ap.error("give --dens --mag --grid --map, or --selftest")
    labels = a.map.split(",")
    if sorted(labels) != ["x", "y", "z"]:
        ap.error("--map must list x, y and z once each, e.g. z,y,x")
    ok, field_ax = anisotropy(a.grid)
    compare_resolution(a.dens, a.mag, a.grid, labels, field_ax)


if __name__ == "__main__":
    main()
